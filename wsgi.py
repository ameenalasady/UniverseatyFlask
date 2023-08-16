from main import app
from tasks import schedule_remove_expired_contacts, notify_open_seats_enqueue
import json
from datetime import datetime

import logging

logging.basicConfig(filename='flasklogs.log', level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')


logging.info("Server starting...")

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

logging.info("Successfully started all saved jobs")


if __name__ == "__main__":
    app.run()
