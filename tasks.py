from datetime import datetime
import requests
from bs4 import BeautifulSoup
import json
from email.message import EmailMessage
import ssl
import smtplib
from apscheduler.schedulers.background import BackgroundScheduler
import pickle
from threading import Lock
from twilio.rest import Client
from dotenv import load_dotenv
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

logging.basicConfig(filename='flasklogs.log', level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

scheduler = BackgroundScheduler()
scheduler.start()


env_path = './keys.env'
load_dotenv(dotenv_path=env_path)

lock = Lock()
lock1 = Lock()

api_endpoint = "https://mytimetable.mcmaster.ca/api/class-data"

login_url = 'https://mytimetable.mcmaster.ca/login.jsp'

data = os.environ.get('LOGINDATA')

headersLogin = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/116.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': 'https://mytimetable.mcmaster.ca',
    'Referer': 'https://mytimetable.mcmaster.ca/'
}


session = requests.Session()


def get_open_seats(courses, term):
    all_courses_results = {}
    with lock:

        try:
            with open("te_values.pickle", "rb") as f:
                t, e = pickle.load(f)
        except (FileNotFoundError, ValueError):
            t, e = 0, 0

        params = {
            'term': str(term),
            't': str(t),
            'e': str(e),
        }

        for i, course in enumerate(courses):
            params[f'course_{i}_0'] = str(course)

        response = session.get(api_endpoint, params=params, timeout=10)

        if "Please correct your device's timezone and time." in response.text or "Not Authorized" in response.text:

            def check_t(t_value):
                params = {
                    'term': str(term),
                    't': str(t_value),
                    'e': str(e),
                }
                for i, course in enumerate(courses):
                    params[f'course_{i}_0'] = str(course)
                response = session.get(api_endpoint, params=params, timeout=10)
                print(f"Trying t={t_value}")
                if "Please correct your device's timezone and time." not in response.text:
                    return t_value
                return None

            def check_e(e_value):
                params = {
                    'term': str(term),
                    't': str(t),
                    'e': str(e_value),
                }
                for i, course in enumerate(courses):
                    params[f'course_{i}_0'] = str(course)
                response = session.get(api_endpoint, params=params, timeout=10)
                print(f"Trying e={e_value}")
                if "Not Authorized" not in response.text:
                    return e_value
                return None

            # Try stored t value and its neighbors before brute forcing
            found_t = False
            for t_value in [t, t+1, t-1, t+2, t-2]:
                result = check_t(t_value)
                if result is not None:
                    # Found correct t value
                    t = result
                    found_t = True
                    break

            if not found_t:
                # Try all other t values until "Check your PC time and timezone" is not seen
                with ThreadPoolExecutor() as executor:
                    futures = [executor.submit(check_t, t_value)
                               for t_value in range(1441)]
                    for future in as_completed(futures):
                        result = future.result()
                        if result is not None:
                            # Found correct t value
                            print(f'Working t value is {result}')
                            t = result
                            # Cancel remaining futures
                            for future in futures:
                                future.cancel()
                            break

            # Try stored e value and its neighbors before brute forcing
            found_e = False
            for e_value in [e, e+3, e+6, e-3, e-6]:
                result = check_e(e_value)
                if result is not None:
                    # Found correct e value
                    e = result
                    found_e = True
                    break

            if not found_e:
                # With previously found t value, try all other e values until "Not Authorized" is not seen
                with ThreadPoolExecutor() as executor:
                    futures = [executor.submit(check_e, e_value)
                               for e_value in range(100)]
                    for future in as_completed(futures):
                        result = future.result()
                        if result is not None:
                            # Found correct e value
                            print(f'Working e value is {result}')
                            e = result
                            # Cancel remaining futures
                            for future in futures:
                                future.cancel()
                            break

            params['t'] = str(t)
            params['e'] = str(e)

            with open("te_values.pickle", "wb") as f:
                pickle.dump((t, e), f)

            response = session.get(api_endpoint, params=params, timeout=10)

        # Store new t and e values in pickle file
        with open("te_values.pickle", "wb") as f:
            pickle.dump((t, e), f)

        print(f"Success with t={t} and e={e}")

        soup = BeautifulSoup(response.text, 'xml')

        for course in courses:
            all_results = {
                'COP': [],
                'PRA': [],
                'PLC': [],
                'WRK': [],
                'LAB': [],
                'PRJ': [],
                'RSC': [],
                'SEM': [],
                'FLD': [],
                'STO': [],
                'IND': [],
                'LEC': [],
                'TUT': [],
                'EXC': [],
                'THE': []
            }

            course_element = soup.find('course', {'key': course})

            if course_element:
                blocks = course_element.find_all('block', {'type': [
                    'COP', 'PRA', 'PLC', 'WRK', 'LAB', 'PRJ', 'RSC', 'SEM', 'FLD', 'STO', 'IND', 'LEC', 'TUT', 'EXC', 'THE']})

                keys = set()

                for block in blocks:
                    section = block['secNo']
                    key = block['key']
                    seats = int(block['os'])
                    block_type = block['type']

                    if key not in keys:
                        all_results[block_type].append({
                            'section': section,
                            'key': key,
                            'open_seats': seats
                        })

                        keys.add(key)

            all_courses_results[course] = all_results

        return json.dumps(all_courses_results)


def remove_expired_contacts():
    with lock1:
        with open('requests.json', 'r') as f:
            data = json.load(f)

        for course in data[:]:
            course_code = course['course_code']
            term = course['term']
            contacts = course['contacts']
            for contact in contacts[:]:
                section_type = contact['type']
                section_code = contact['section']
                contact_method = contact['contact_method']
                contact_info_list = contact['contact_info']
                expires_at_list = contact['expires_at']
                for i in range(len(contact_info_list)-1, -1, -1):
                    contact_info = contact_info_list[i]
                    expires_at = datetime.strptime(
                        expires_at_list[i], '%Y-%m-%d %H:%M:%S')
                    if datetime.now() > expires_at:
                        # Send a message to the user informing them that their subscription has expired
                        message = f"Your subscription for {course_code} {section_type} {section_code} has expired! Feel free to resubscribe at https://www.universeaty.ca/ to continue checking."
                        if contact_method == 'email':
                            send_email(contact_info, message)
                        elif contact_method == 'phone':
                            send_sms(contact_info, message)
                        logging.info(
                            f"Sent expiration notification: {message} to {contact_info}")
                        del contact_info_list[i]
                        del expires_at_list[i]
                        logging.info(
                            f"Removed {contact_info}, {course_code}, {section_code}, {expires_at}")

                # If the contact has no more contact_info, remove it from the contacts
                if not contact_info_list:
                    contacts.remove(contact)
                    logging.info(
                        f"Removed contact from section {section_code}")

            # If the course has no more contacts, remove it from the data
            if not contacts:
                data.remove(course)
                logging.info(f"Removed course {course_code} from data")

        with open('requests.json', 'w') as f:
            json.dump(data, f, indent=4)


def schedule_remove_expired_contacts():
    scheduler.add_job(remove_expired_contacts, 'interval', seconds=45)


def enqueue_jobs():
    scheduler.add_job(process_requests, 'interval', seconds=10)


def process_requests():

    open_seats_3202510 = {}
    open_seats_3202340 = {}
    open_seats_3202450 = {}  # New term

    # Load requests from requests.json
    with open('requests.json', 'r') as f:
        requests = json.load(f)

    # Segregate course codes into 3202340, 3202510 or 3202450
    term_3202340 = []
    term_3202510 = []
    term_3202450 = []  # New term

    for request in requests:
        if request['term'] == '3202340':
            term_3202340.append(request['course_code'])
        elif request['term'] == '3202510':
            term_3202510.append(request['course_code'])
        elif request['term'] == '3202450':  # New term
            term_3202450.append(request['course_code'])

    session.post(login_url, data=data, headers=headersLogin, timeout=10)

    # Get open seats for each term
    if term_3202340:
        open_seats_3202340 = json.loads(
            get_open_seats(term_3202340, '3202340'))
    if term_3202510:
        open_seats_3202510 = json.loads(
            get_open_seats(term_3202510, '3202510'))
    if term_3202450:  # New term
        open_seats_3202450 = json.loads(
            get_open_seats(term_3202450, '3202450'))

    # Process open seats for each term
    change_made = False
    for request in requests:
        course_code = request['course_code']
        if request['term'] == '3202340':
            open_seats = open_seats_3202340[course_code]
        elif request['term'] == '3202510':
            open_seats = open_seats_3202510[course_code]
        elif request['term'] == '3202450':  # New term
            open_seats = open_seats_3202450[course_code]

        for section_type, sections in open_seats.items():
            for section in sections:
                if section['open_seats'] > 0:
                    section_code = section['section']
                    open_seats_count = section['open_seats']
                    logging.info(
                        f"Found {open_seats_count} open seats for {course_code}: {section_type} {section_code}")
                    # Send email to all contacts for this course code and section
                    contacts = [contact for contact in request['contacts'] if contact['type']
                                == section_type and contact['section'] == section_code]
                    message = f"There are {open_seats_count} open seats for {course_code}: {section_type} {section_code}! Tracking will now stop."
                    for contact in contacts:
                        for email in contact['contact_info']:
                            send_email(email, message)
                            logging.info(f"Sent email to {email}: {message}")
                        # Delete section from course code in requests.json
                        request['contacts'].remove(contact)
                        change_made = True
                        logging.info(
                            f"Deleted section {section_code} from course code {course_code} in requests.json")

    # Save updated requests to requests.json only if a change was made
    if change_made:
        with open('requests.json', 'w') as f:
            json.dump(requests, f)


def sendConfirmationEmails(courses):
    # Send a message to the user informing them that they have subscribed to notifications for all the specified courses
    message = "You have subscribed to notifications for the following courses:\n\n"
    for course in courses:
        course_code, section_type, section_code, contact_method, contact_info, expires_at_et_str = course
        message += f"{course_code} {section_type} {section_code} until {expires_at_et_str}\n\n"
    message += "Please note that duplicate requests will be ignored."
    if contact_method == 'email':
        send_email(contact_info, message)
    elif contact_method == 'phone':
        send_sms(contact_info, message)
    print(f"Sent subscription confirmation: {message}")


def send_email(email_address, message):
    emailSender = "opencoursealert@gmail.com"
    password = os.environ.get('PASSWORD')
    emailReceiver = email_address
    subject = "Course Alert"
    body = message

    em = EmailMessage()
    em['From'] = emailSender
    em['To'] = emailReceiver
    em['Subject'] = subject
    em.set_content(body)

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as smtp:
        smtp.login(emailSender, password)
        smtp.sendmail(emailSender, emailReceiver, em.as_string())


def send_sms(phone_number, message):
    auth_token = os.environ.get('AUTH_TOKEN')
    account_sid = os.environ.get('ACCOUNT_SID')

    client = Client(account_sid, auth_token)

    message = client.messages.create(
        from_='XXXXXXXXXXX',
        body=message,
        to=int('+1'+str(phone_number))
    )
