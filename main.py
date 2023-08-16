import json
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from datetime import datetime, timedelta
import re
from pytz import timezone as timezonepytz


from tasks import get_open_seats, notify_open_seats_enqueue, sendConfirmationEmail, schedule_remove_expired_contacts

app = Flask(__name__)
CORS(app)


@app.route('/')
def ping():
    return 'pong'


@app.route('/open_seats')
def open_seats():
    course_code = request.args.get('course_code')
    term = request.args.get('term')

    # Call the get_open_seats function directly
    result = get_open_seats(course_code, term)

    # Return the result to the user
    return jsonify(json.loads(result))


@app.route('/notify_open_seats')
def notify_open_seats():
    course_code = request.args.get('course_code')
    term = request.args.get('term')
    section = request.args.get('section')
    contact_method = request.args.get('contact_method')
    contact_info = request.args.get('contact_info')

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

    # Check if the entry already exists in the JSON file
    entry_exists = any(request for request in requests if request['course_code'] == course_code and request['term'] == term and request['section'] == section and any(
        contact for contact in request['contacts'] if contact['contact_method'] == contact_method and contact['contact_info'] == contact_info))

    if not entry_exists:
        utc_tz = timezonepytz('UTC')
        et_tz = timezonepytz('US/Eastern')
        expires_at_pytz = utc_tz.localize(expires_at)
        expires_at_et = expires_at_pytz.astimezone(et_tz)
        # Format the expires_at time in ET using 12-hour format with AM/PM
        expires_at_et_str = expires_at_et.strftime('%Y-%m-%d %I:%M:%S %p %Z')

        # Check if there is already an entry with the same course_code, term, and section
        existing_entry = next((request for request in requests if request['course_code'] == course_code and request['term'] ==
                               term and request['section'] == section), None)

        if existing_entry:
            # If there is an existing entry, append the new contact information to it
            existing_entry['contacts'].append({'contact_method': contact_method, 'contact_info': contact_info,
                                               'expires_at': expires_at.strftime('%Y-%m-%d %H:%M:%S')})
        else:
            # If there is no existing entry, create a new one
            requests.append({'course_code': course_code, 'term': term, 'section': section,
                            'contacts': [{'contact_method': contact_method, 'contact_info': contact_info,
                                          'expires_at': expires_at.strftime('%Y-%m-%d %H:%M:%S')}]})

            notify_open_seats_enqueue(course_code, term, section)

        sendConfirmationEmail(
            course_code, section, contact_method, contact_info, expires_at_et_str)

        with open('requests.json', 'w') as f:
            json.dump(requests, f)

        # Return a response with status code 200
        response = make_response('Success', 200)
    else:
        # Return a response with status code 400 (Bad Request)
        response = make_response('Duplicate', 400)

    return response


if __name__ == '__main__':

    schedule_remove_expired_contacts()

    with open('requests.json', 'r') as f:
        requests = json.load(f)

    for request in requests:
        course_code = request['course_code']
        term = request['term']
        section = request['section']
        contacts = request['contacts']

        print(f"Course: {course_code}, Term: {term}, Section: {section}")
        print("Contacts:")
        for contact in contacts:
            contact_method = contact['contact_method']
            contact_info = contact['contact_info']
            expires_at = contact['expires_at']
            print(
                f"\tContact Method: {contact_method}, Contact Info: {contact_info}, Expires At: {expires_at}")

        notify_open_seats_enqueue(course_code, term, section)

    app.run(host='0.0.0.0', port=5000, debug=False)
