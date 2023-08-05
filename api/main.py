import json
from flask import Flask, jsonify, request, make_response
from flask_cors import CORS
from datetime import datetime, timedelta


from tasks import get_open_seats, notify_open_seats_enqueue

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

    # Calculate the expiration time for the worker (24 hours from now in UTC)
    expires_at = datetime.utcnow() + timedelta(hours=24)

    notify_open_seats_enqueue(
        course_code, term, section, contact_method, contact_info, expires_at, True)

    # Store the details in a JSON file
    try:
        with open('requests.json', 'r') as f:
            requests = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        requests = []

    requests.append({'course_code': course_code, 'term': term, 'section': section,
                     'contact_method': contact_method, 'contact_info': contact_info,
                     'expires_at': expires_at.strftime('%Y-%m-%d %H:%M:%S')})

    with open('requests.json', 'w') as f:
        json.dump(requests, f)

    # Return a response with status code 200
    response = make_response('', 200)
    return response


if __name__ == '__main__':

    try:
        with open('requests.json', 'r') as f:
            requests = json.load(f)
    except json.JSONDecodeError:
        requests = []

    for oneRequest in requests:

        notify_open_seats_enqueue(oneRequest['course_code'], oneRequest['term'], oneRequest['section'],
                                  oneRequest['contact_method'], oneRequest['contact_info'],
                                  datetime.strptime(oneRequest['expires_at'], '%Y-%m-%d %H:%M:%S'), False)

    app.run(host='0.0.0.0', port=5000, debug=False)
