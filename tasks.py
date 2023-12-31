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

logging.basicConfig(filename='flasklogs.log', level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

scheduler = BackgroundScheduler()
scheduler.start()


env_path = './keys.env'
load_dotenv(dotenv_path=env_path)

lock = Lock()

api_endpoint = "https://mytimetable.mcmaster.ca/getclassdata.jsp"

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


def get_open_seats(course, term):

    with lock:

        try:
            with open("te_values.pickle", "rb") as f:
                t, e = pickle.load(f)
        except (FileNotFoundError, ValueError):
            t, e = 0, 0

        def check_t(t_value):
            params = {
                'term': "3202340",
                'course_0_0': "COMPSCI-1XC3",
                't': str(t_value),
                'e': str(e),
            }
            response = session.get(api_endpoint, params=params, timeout=10)
            print(f"Trying t={t_value}")
            if "Check your PC time and timezone" not in response.text:
                return t_value
            return None

        def check_e(e_value):
            params = {
                'term': "3202340",
                'course_0_0': "COMPSCI-1XC3",
                't': str(t),
                'e': str(e_value),
            }
            response = session.get(api_endpoint, params=params, timeout=10)
            print(f"Trying e={e_value}")
            if "Not Authorized" not in response.text:
                return e_value
            return None

        # Try stored t value and its neighbors before brute forcing
        found_t = False
        for t_value in [t, t+1, t+2, t-1, t-2]:
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
                        e = result
                        # Cancel remaining futures
                        for future in futures:
                            future.cancel()
                        break

        # Store new t and e values in pickle file
        with open("te_values.pickle", "wb") as f:
            pickle.dump((t, e), f)

        print(f"Success with t={t} and e={e}")

        all_results = {
            'LEC': [],
            'LAB': [],
            'TUT': []
        }

        params = {
            'term': str(term),
            'course_0_0': str(course),
            't': str(t),
            'e': str(e),
        }

        response = session.get(api_endpoint, params=params, timeout=10)

        # print(response.text)

        soup = BeautifulSoup(response.text, 'xml')

        blocks = soup.find_all('block', {'type': ['LEC', 'LAB', 'TUT']})

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

        return (json.dumps(all_results))


def remove_expired_contacts():
    with open('requests.json', 'r') as f:
        data = json.load(f)

    for course in data[:]:
        course_code = course['course_code']
        term = course['term']
        contacts = course['contacts']
        for contact in contacts[:]:
            section = contact['section']
            contact_method = contact['contact_method']
            contact_info_list = contact['contact_info']
            expires_at_list = contact['expires_at']
            for i in range(len(contact_info_list)-1, -1, -1):
                contact_info = contact_info_list[i]
                expires_at = datetime.strptime(
                    expires_at_list[i], '%Y-%m-%d %H:%M:%S')
                if datetime.now() > expires_at:
                    # Send a message to the user informing them that their subscription has expired
                    message = f"Your subscription for {course_code} section {section} has expired!"
                    if contact_method == 'email':
                        send_email(contact_info, message)
                    elif contact_method == 'phone':
                        send_sms(contact_info, message)
                    logging.info(
                        f"Sent expiration notification: {message} to {contact_info}")
                    del contact_info_list[i]
                    del expires_at_list[i]
                    logging.info(
                        f"Removed {contact_info}, {course_code}, {section}, {expires_at}")

            # If the contact has no more contact_info, remove it from the contacts
            if not contact_info_list:
                contacts.remove(contact)
                logging.info(f"Removed contact from section {section}")

        # If the course has no more contacts, remove it from the data
        if not contacts:
            data.remove(course)
            logging.info(f"Removed course {course_code} from data")

    with open('requests.json', 'w') as f:
        json.dump(data, f, indent=4)


def schedule_remove_expired_contacts():
    scheduler.add_job(remove_expired_contacts, 'interval', minutes=1)


def process_scraped_json():

    session.post(login_url, data=data, headers=headersLogin, timeout=10)

    with open('requests.json', 'r') as f:
        requests = json.load(f)
    for request in requests:
        course_code = request['course_code']
        term = request['term']
        check_open_seats_enqueue(course_code, term)


def enqueue_jobs():
    scheduler.add_job(process_scraped_json, 'interval', seconds=90)


def check_open_seats_enqueue(course_code, term):
    result = json.loads(get_open_seats(course_code, term))

    with open('requests.json', 'r') as f:
        requests = json.load(f)

    # Find the entry with the specified course_code and term
    entry = next((request for request in requests if request['course_code'] == course_code and request['term'] ==
                  term), None)

    if entry:
        for contact in entry['contacts']:
            section = contact['section']
            # Check if there are open seats for the specified section
            lec_section = next(
                (lec for lec in result['LEC'] if lec['section'] == section), None)
            lab_section = next(
                (lab for lab in result['LAB'] if lab['section'] == section), None)
            tut_section = next(
                (tut for tut in result['TUT'] if tut['section'] == section), None)

            if lec_section:
                logging.info(f"Searched: {course_code:<14} {lec_section}")
            if lab_section:
                logging.info(f"Searched: {course_code:<14} {lab_section}")
            if tut_section:
                logging.info(f"Searched: {course_code:<14} {tut_section}")

            # Determine which section has open seats
            open_seats_section = None
            open_seats_count = 0
            if lec_section and lec_section['open_seats'] > 0:
                open_seats_section = lec_section
                open_seats_count = lec_section['open_seats']
            elif lab_section and lab_section['open_seats'] > 0:
                open_seats_section = lab_section
                open_seats_count = lab_section['open_seats']
            elif tut_section and tut_section['open_seats'] > 0:
                open_seats_section = tut_section
                open_seats_count = tut_section['open_seats']

            if open_seats_section:
                # If there are open seats, send a notification to all users who have requested to be notified
                with open('requests.json', 'r') as f:
                    requests = json.load(f)

                # Find the entry with the specified course_code and term
                entry = next((request for request in requests if request['course_code'] == course_code and request['term'] ==
                              term), None)
                if entry:
                    # Loop through all contacts in the entry's contacts list
                    for contact in entry['contacts']:
                        if contact['section'] == section:
                            contact_method = contact['contact_method']
                            contact_info_list = contact['contact_info']
                            expires_at_list = contact['expires_at']
                            for i in range(len(contact_info_list)-1, -1, -1):
                                contact_info = contact_info_list[i]

                                # Send a notification to the user using their specified contact method
                                message = f"There are {open_seats_count} open seats for {course_code} section {section}! Tracking will now stop."
                                if contact_method == 'email':
                                    send_email(contact_info, message)
                                    logging.info(
                                        f'Sent "{message}" to {contact_info}.')
                                elif contact_method == 'phone':
                                    send_sms(contact_info, message)
                                    logging.info(
                                        f'Sent "{message}" to {contact_info}.')
                                print(
                                    f"Sent notification: {message} to {contact_info}")

                                del contact_info_list[i]
                                del expires_at_list[i]

                            # Update the contact_info and expires_at lists in the requests variable unconditionally
                            contact['contact_info'] = contact_info_list
                            contact['expires_at'] = expires_at_list

                    entry['contacts'] = [contact for contact in entry['contacts']
                                         if contact['contact_info'] or contact['expires_at']]

                    # Unconditionally write the updated requests variable to requests.json
                    with open('requests.json', 'w') as f:
                        json.dump(requests, f)

            else:
                print(
                    f"{course_code}, {term}, {section}: No open seats found")


def sendConfirmationEmail(course_code, section, contact_method, contact_info, expires_at_et_str):
    # Send a message to the user informing them that they have subscribed to notifications for the specified course
    message = f"You have subscribed to notifications for {course_code} section {section} until {expires_at_et_str}!"
    if contact_method == 'email':
        send_email(contact_info, message)
    elif contact_method == 'phone':
        send_sms(contact_info, message)
    print(f"Sent subscription confirmation: {message}")


def printAllJobs():

    jobs = scheduler.get_jobs()
    for job in jobs:
        print(f"Job ID: {job.id}")
        print(f"Next run time: {job.next_run_time}")
        print(f"Job function: {job.func.__name__}")
        print(f"Job arguments: {job.args}")
        print()


def returnAllJobs():
    return scheduler.get_jobs()


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
        from_='+12267410437',
        body=message,
        to=int('+1'+str(phone_number))
    )
