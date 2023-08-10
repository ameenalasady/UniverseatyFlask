from datetime import datetime
from datetime import datetime, timedelta
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import json
from email.message import EmailMessage
import ssl
import smtplib
from apscheduler.schedulers.background import BackgroundScheduler
import pickle
import signal
import sys
from threading import Lock
from twilio.rest import Client
import os
from pytz import timezone as timezonepytz

scheduler = BackgroundScheduler()
scheduler.start()


lock = Lock()


local_dict = {}


def signal_handler(sig, frame):
    save_local_dict()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def save_local_dict():
    with open('local_dict.pickle', 'wb') as handle:
        pickle.dump(local_dict, handle, protocol=pickle.HIGHEST_PROTOCOL)


def load_local_dict():
    global local_dict
    try:
        with open('local_dict.pickle', 'rb') as handle:
            local_dict = pickle.load(handle)
    except FileNotFoundError:
        pass


load_local_dict()


def get_open_seats(course_code, term):
    with lock:

        base_url = 'https://mytimetable.mcmaster.ca/getclassdata.jsp'

        t = calculateT()

        all_results = {
            'LEC': [],
            'LAB': [],
            'TUT': []
        }

        e_value = local_dict.get('e_value')
        if e_value is not None:
            e_value = int(e_value)
            e_values_to_try = [e_value, e_value + 3,
                               e_value + 6, e_value - 3, e_value - 6]
            for e in e_values_to_try:
                print(f"Trying value of e: {e}")

                params = {
                    'term': term,
                    'course_0_0': course_code,
                    't': str(t),
                    'e': str(e),
                }

                response = requests.get(base_url, params=params)

                if "Check your PC time and timezone" in response.text:
                    print(
                        "Response contains Check your PC time and timezone. Trying values of t from 0 to 2000.")
                    for t in range(1441):
                        print(f"Trying value of t: {t}")

                        params['t'] = str(t)

                        response = requests.get(base_url, params=params)

                        if "Check your PC time and timezone" not in response.text:
                            print("Found valid value of t!")
                            now = datetime.now(timezone.utc)
                            utc_plus_4 = now + timedelta(hours=4)
                            t_diff = t - (utc_plus_4.hour *
                                          60 + utc_plus_4.minute)
                            local_dict['t_diff'] = t_diff
                            break

                if "Not Authorized" not in response.text:
                    print("Found valid value of e!")
                    local_dict['e_value'] = e
                    break

            else:
                print(
                    "All values of e in e_values_to_try gave Not Authorized. Resetting e_value to None and trying 0 to 99 again.")
                local_dict.pop('e_value', None)

        if local_dict.get('e_value') is None:
            for e in range(100):
                print(f"Trying value of e: {e}")

                params = {
                    'term': term,
                    'course_0_0': course_code,
                    't': str(t),
                    'e': str(e),
                }

                response = requests.get(base_url, params=params)

                if "Check your PC time and timezone" in response.text:
                    print(
                        "Response contains Check your PC time and timezone. Trying values of t from 0 to 2000.")
                    for t in range(2001):
                        print(f"Trying value of t: {t}")

                        params['t'] = str(t)

                        response = requests.get(base_url, params=params)

                        if "Check your PC time and timezone" not in response.text:
                            print("Found valid value of t!")
                            now = datetime.now(timezone.utc)
                            utc_plus_4 = now + timedelta(hours=4)
                            t_diff = t - (utc_plus_4.hour *
                                          60 + utc_plus_4.minute)
                            local_dict['t_diff'] = t_diff
                            break

                if "Not Authorized" not in response.text:
                    print("Found valid value of e!")
                    local_dict['e_value'] = e
                    break

    soup = BeautifulSoup(response.text, 'xml')

    blocks = soup.find_all('block', {'type': ['LEC', 'LAB', 'TUT']})

    keys = set()

    for block in blocks:
        section = block['secNo']
        key = block['key']
        seats = int(block['os'])
        block_type = block['type']

        # print(f"Found section: {section}, key: {key}, open seats: {seats}")

        if key not in keys:
            all_results[block_type].append({
                'section': section,
                'key': key,
                'open_seats': seats
            })

            keys.add(key)

    return json.dumps(all_results)


def calculateT():
    now = datetime.now(timezone.utc)

    utc_plus_4 = now + timedelta(hours=4)

    minutes = utc_plus_4.hour * 60 + utc_plus_4.minute

    t_diff = local_dict.get('t_diff')
    if t_diff is not None:
        minutes += int(t_diff)

    return minutes


def notify_open_seats_enqueue(course_code, term, section, contact_method, contact_info, expires_at, initiate):

    if initiate:

        utc_tz = timezonepytz('UTC')

        et_tz = timezonepytz('US/Eastern')

        expires_at_pytz = utc_tz.localize(expires_at)

        expires_at_et = expires_at_pytz.astimezone(et_tz)

        # Format the expires_at time in ET using 12-hour format with AM/PM

        expires_at_et_str = expires_at_et.strftime('%Y-%m-%d %I:%M:%S %p %Z')

        # Send a message to the user informing them that they have subscribed to notifications for the specified course
        message = f"You have subscribed to notifications for {course_code} section {section} until {expires_at_et_str}!"
        if contact_method == 'email':
            send_email(contact_info, message)
        elif contact_method == 'phone':
            send_sms(contact_info, message)
        print(f"Sent subscription confirmation: {message}")

    interval = 60  # seconds
    duration = 24 * 60 * 60  # seconds
    repeat = duration // interval

    scheduler.add_job(check_open_seats_enqueue, 'interval', seconds=10, args=(
        course_code, term, section, contact_method, contact_info, expires_at))


def check_open_seats_enqueue(course_code, term, section, contact_method, contact_info, expires_at):
    # Check if the current time is past the expires_at time
    if datetime.utcnow() > expires_at:

        # Send a message to the user informing them that their subscription has expired
        message = f"Your subscription for {course_code} section {section} has expired!"
        if contact_method == 'email':
            send_email(contact_info, message)
        elif contact_method == 'phone':
            send_sms(contact_info, message)
        print(f"Sent expiration notification: {message}")

        # Remove the scheduled job
        for job in scheduler.get_jobs():
            # Check if the job has the specified arguments
            if job.args == (course_code, term, section, contact_method, contact_info, expires_at):
                # Remove the job
                scheduler.remove_job(job.id)
                print(f"Removed job with id: {job.id}")

        # Delete the entry from pyapitest.json
        with open('pyapitest.json', 'r') as f:
            data = json.load(f)
        data = [entry for entry in data if not (
            entry['course_code'] == str(course_code) and entry['term'] == str(term) and entry['section'] == str(section) and entry['contact_info'] == str(contact_info) and entry['contact_method'] == str(contact_method))]
        with open('pyapitest.json', 'w') as f:
            json.dump(data, f)
    else:
        # Call the get_open_seats function directly
        result = json.loads(get_open_seats(course_code, term))

        # Check if there are open seats for the specified section
        lec_section = next(
            (lec for lec in result['LEC'] if lec['section'] == section), None)
        if lec_section and lec_section['open_seats'] > 0:
            # If there are open seats, send a notification to the user using their specified contact method
            message = f"There are {lec_section['open_seats']} open seats for {course_code} section {section}! Tracking will now stop."
            if contact_method == 'email':
                send_email(contact_info, message)
            elif contact_method == 'phone':
                send_sms(contact_info, message)
            print(f"Sent notification: {message}")

            for job in scheduler.get_jobs():
                # Check if the job has the specified arguments
                if job.args == (course_code, term, section, contact_method, contact_info, expires_at):
                    # Remove the job
                    scheduler.remove_job(job.id)

            # Delete the entry from pyapitest.json
            with open('pyapitest.json', 'r') as f:
                data = json.load(f)
            data = [entry for entry in data if not (
                entry['course_code'] == str(course_code) and entry['term'] == str(term) and entry['section'] == str(section) and entry['contact_info'] == str(contact_info) and entry['contact_method'] == str(contact_method))]
            with open('pyapitest.json', 'w') as f:
                json.dump(data, f)

        else:
            print(
                f"{course_code}, {term}, {section}, {contact_info}: No open seats found")


def printAllJobs():

    jobs = scheduler.get_jobs()
    for job in jobs:
        print(f"Job ID: {job.id}")
        print(f"Next run time: {job.next_run_time}")
        print(f"Job function: {job.func.__name__}")
        print(f"Job arguments: {job.args}")
        print()


def send_email(email_address, message):
    emailSender = "opencoursealert@gmail.com"
    password = str(os.environ.get('PASSWORD'))
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
    fromPhone = str(os.environ.get('FROM'))

    client = Client(account_sid, auth_token)

    message = client.messages.create(
        from_=fromPhone,
        body=message,
        to=int('+1'+str(phone_number))
    )
