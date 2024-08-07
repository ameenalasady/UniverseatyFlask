import json
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime, timedelta
import re
from pytz import timezone as timezonepytz
import logging
import ast


from tasks import get_open_seats, sendConfirmationEmails, lock1
from tasks import session, login_url, data, headersLogin

app = Flask(__name__)
CORS(app)


def get_client_ip():
    x_forwarded_for = request.headers.get('X-Forwarded-For')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0]
    else:
        return get_remote_address()


limiter = Limiter(app=app, key_func=get_client_ip,
                  default_limits=["200 per day", "50 per hour"], storage_uri="memory://",)


logging.basicConfig(filename='flasklogs.log', level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')


@app.route('/')
def ping():
    return 'pong'


@app.route('/open_seats')
def open_seats():
    course_code = [request.args.get('course_code')]
    term = request.args.get('term')

    x_forwarded_for = request.headers.get('X-Forwarded-For')
    client_ip = x_forwarded_for.split(',')[0] if x_forwarded_for else None

    # Log the endpoint access
    logging.info(
        f"Endpoint /open_seats accessed from IP {client_ip} with arguments course_code={course_code}, term={term}")

    # Check if course_code and term are not None or empty strings
    if not course_code or not term or not any(course_code) or term.strip() == '':
        # Return a 400 Bad Request response
        return make_response('Bad Request', 400)
    # session.post(login_url, data=data, headers=headersLogin, timeout=10)

    # Call the get_open_seats function directly
    result = get_open_seats(course_code, term)

    # Check if the result is an empty dictionary
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

    if json.loads(result) == all_results:
        # Run the get_open_seats function again
        result = get_open_seats(course_code, term)

    # Return the result to the user
    return jsonify(json.loads(result))


@app.route('/notify_open_seats')
@limiter.limit("50/hour", override_defaults=True)
def notify_open_seats():
    with lock1:
        course_code = request.args.get('course_code')
        term = request.args.get('term')
        sections = ast.literal_eval(request.args.get('section'))
        contact_method = request.args.get('contact_method')
        contact_info = request.args.get('contact_info')

        x_forwarded_for = request.headers.get('X-Forwarded-For')
        client_ip = x_forwarded_for.split(',')[0] if x_forwarded_for else None

        # Log the endpoint access
        logging.info(
            f"Endpoint /notify_open_seats accessed from IP {client_ip} with arguments course_code={course_code}, term={term}, section={sections}, contact_method={contact_method}, contact_info={contact_info}")

        # Check if course_code, term, section, contact_method, and contact_info are not None or empty strings
        if not course_code or not term or not contact_method or not contact_info or course_code.strip() == '' or term.strip() == '' or contact_method.strip() == '' or contact_info.strip() == '':
            # Return a 400 Bad Request response
            return make_response('Bad Request', 400)

        if any(not item or item.strip() == '' for sublist in sections for item in sublist):
            # Return a 400 Bad Request response
            return make_response('Bad Request', 400)

        with open(f'{term}.json', 'r') as f:
            courses = json.load(f)
            for course in courses:
                if course['Text'] == course_code:
                    break
            else:
                return make_response('Course Error', 400)

        # Validate the contact_info based on the contact_method
        if contact_method == 'email':
            # Check if the email is valid using a regular expression
            email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            if not re.fullmatch(email_regex, contact_info):
                # Return a response with status code 400 (Bad Request)
                response = make_response('Invalid Email', 400)
                return response
        elif contact_method == 'phone':
            # Check if the phone number is valid using a regular expression
            phone_regex = r'^\+?1?\d{9,15}$'
            if not re.fullmatch(phone_regex, contact_info):
                # Return a response with status code 400 (Bad Request)
                response = make_response('Invalid Phone Number', 400)
                return response

    # Calculate the expiration time for the worker
    expires_at = datetime.utcnow() + timedelta(days=7)

    # Store the details in a JSON file
    try:
        with open('requests.json', 'r') as f:
            requests = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        requests = []

    # Load the course info from the file
    with open(f'courseInfo/{course_code}_{term}.json', 'r') as f:
        course_info = json.load(f)

    courses = []

    for section in sections:
        section_type = section[0]
        section_code = section[1]
        section_key = section[2]

        # logging.info(
        #     f"looking for {section_type}, {section_code}, {section_key} in {course_info}")

        if not (any(entry for entry in course_info[section_type] if entry['section'] == section_code and entry['key'] == section_key)):
            continue

        entry_exists = any(request for request in requests if request['course_code'] == course_code and request['term'] == term and any(
            contact for contact in request['contacts'] if contact['type'] == section_type and contact['section'] == section_code and contact['contact_method'] == contact_method and contact_info in contact['contact_info']))

        if not entry_exists:
            utc_tz = timezonepytz('UTC')
            et_tz = timezonepytz('US/Eastern')
            expires_at_pytz = utc_tz.localize(expires_at)
            expires_at_et = expires_at_pytz.astimezone(et_tz)
            # Format the expires_at time in ET using 12-hour format with AM/PM
            expires_at_et_str = expires_at_et.strftime(
                '%Y-%m-%d %I:%M:%S %p %Z')

            existing_entry = next((request for request in requests if request['course_code'] == course_code and request['term'] ==
                                   term), None)
            if existing_entry:
                # If there is an existing entry, check if there is already an entry with the same section and contact_method
                existing_contact = next(
                    (contact for contact in existing_entry['contacts'] if contact['type'] == section_type and contact['section'] == section_code and contact['contact_method'] == contact_method), None)

                if existing_contact:
                    # If there is an existing contact, append the new contact information to it
                    existing_contact['contact_info'].append(contact_info)
                    existing_contact['expires_at'].append(
                        expires_at.strftime('%Y-%m-%d %H:%M:%S'))
                else:
                    # If there is no existing contact, create a new one
                    existing_entry['contacts'].append({'type': section_type, 'section': section_code, 'contact_method': contact_method, 'contact_info': [contact_info],
                                                       'expires_at': [expires_at.strftime('%Y-%m-%d %H:%M:%S')]})
            else:
                # If there is no existing entry, create a new one
                requests.append({'course_code': course_code, 'term': term,
                                'contacts': [{'type': section_type, 'section': section_code, 'contact_method': contact_method, 'contact_info': [contact_info],
                                              'expires_at': [expires_at.strftime('%Y-%m-%d %H:%M:%S')]}]})

            courses.append((course_code, section_type, section_code,
                           contact_method, contact_info, expires_at_et_str))

    if courses:
        sendConfirmationEmails(courses)

    with open('requests.json', 'w') as f:
        json.dump(requests, f)

    # Return a response with status code 200
    response = make_response('Success', 200)
    return response
