import mysql.connector
import random
import smtplib
import threading

from email.mime.text import MIMEText
import email.utils
import os
from dotenv import load_dotenv
load_dotenv("_keys.env")

class Database:
    def __init__(self):
        # Establish MySQL connection and initialize the cursor
        self.connection = mysql.connector.connect(
            host="stiprint.mysql.pythonanywhere-services.com",
            user="stiprint",
            password=os.environ.get("MYSQL_PASSWORD"),
            database="stiprint$stiprintdb",
            connection_timeout=10
        )
        self.cursor = self.connection.cursor(buffered=True)
        self.Notifications = Notifications()

    def get_cursor(self):
        # Ensure the database connection is still alive
        try:
            self.connection.ping(reconnect=True)
        except Exception as e:
            print("Reconnecting to MySQL:", e)
        return self.cursor

    def execute(self, query):
        _exe = self.get_cursor().execute(query)
        self.connection.commit()
        return self.cursor

    def add_student(self, email: str, name: str, student_id: str):
        try:
            # Check if the student is in the paidstudents table
            isPaid = self.execute(f"SELECT student_id FROM paidstudents WHERE student_id = '{student_id}'").fetchone()
            isPaid = 1 if isPaid else 0

            # Determine if the person is Faculty or Student from the name string
            isFacultyOrStudent = name.split(" ")[-1].replace("(", "").replace(")", "")

            # Only insert if user does not already exist
            if isFacultyOrStudent == "Faculty":
                if self.execute(f'SELECT * FROM userlist WHERE email = "{email}"').fetchone() is None:
                    self.execute(f'INSERT INTO userlist VALUES("{email}", "{name}", "0", 1, 1, "none")')
            elif isFacultyOrStudent == "Student":
                if self.execute(f'SELECT * FROM userlist WHERE email = "{email}"').fetchone() is None:
                    self.execute(f'INSERT INTO userlist VALUES("{email}", "{name}", "{student_id}", {isPaid}, 0, "none")')
        except Exception:
            pass  # Ignore errors if user already exists

    def check_student_paid(self, student_id):
        try:
            # Check if student exists in paidstudents table
            isPaid = self.execute(f"SELECT student_id FROM paidstudents WHERE student_id = '{student_id}'").fetchone()
            if isPaid is not None:
                # Further check userlist to see if isPaid is marked
                isPaid2 = self.execute(f"SELECT isPaid FROM userlist WHERE student_id = '{student_id}'").fetchone()
                if isPaid2 is not None:
                    return isPaid2[0] == 1
                else:
                    return True
            else:
                return False
        except Exception as e:
            print(e)

    def get_appointments(self, email=None):
        if email is None:
            # Sort appointments globally by status, type, date, and time
            fetched_appointments = self.execute("""
                SELECT *
                FROM appointments
                ORDER BY
                    CASE WHEN status = 'Pending' THEN 1
                         WHEN status = 'Done' THEN 2
                         ELSE 3 END DESC,

                    CASE WHEN type = 'URGENT' THEN 1
                         WHEN type = 'MINOR' THEN 3
                         ELSE 2 END DESC,

                    STR_TO_DATE(date, '%d %M, %Y') DESC,
                    STR_TO_DATE(time, '%h:%i %p') DESC;
            """).fetchall()
        else:
            # Get appointments specific to a user
            fetched_appointments = self.execute(f"""
                SELECT *
                FROM appointments
                WHERE email = '{email}'
                ORDER BY
                    CASE WHEN status = 'Pending' THEN 1
                         WHEN status = 'Done' THEN 2
                         ELSE 3 END DESC;
            """).fetchall()

        appointments = []
        if len(fetched_appointments) != 0:
            for i in fetched_appointments:
                appointment = {
                    "email": i[0],
                    "name": i[1],
                    "file": i[2],
                    "copies": i[3],
                    "size": i[4],
                    "type": i[5],
                    "date": i[6],
                    "time": i[7],
                    "status": i[8],
                    "paymentAmount": i[9],
                    "request_id": i[10]
                }
                appointments.append(appointment)
            appointments.reverse()  # Return latest last
            return appointments
        else:
            return False

    def cancel_appointment(self, _email, request_id):
        # Mark appointment as cancelled in the database
        self.execute(f"UPDATE appointments SET status = 'Cancelled' WHERE email = '{_email}' AND request_id = {request_id}")

        # Attempt to send a cancellation email to both primary and secondary email addresses
        isUserHasSecondaryEmail = self.execute(f"SELECT secondaryEmail FROM userlist WHERE email = '{_email}'").fetchone()
        subject = "[STI College Davao] Your appointment was Cancelled"

        if isUserHasSecondaryEmail is not None:
            name = self.get_name_by_email(_email)
            text = MIMEText(f"""
            Hi, {name}

            You cancelled your appointment.
            """)
            text["Subject"] = subject
            text["From"] = self.Notifications.gmail_user
            text["To"] = _email
            text["Date"] = email.utils.formatdate(localtime=True)

            # Run email sending in a new thread to avoid blocking main thread
            threading.Thread(target=self.Notifications.send_user_email,
                             args=([_email, isUserHasSecondaryEmail[0]], text.as_string())).start()

    def add_appointment(self, _email: str, name: str, file: str, size: str, copies: int, type: str, date: str, time: str):
        # Generate a unique request ID for appointment
        _request_id = str(random.randint(100000, 999999))
        self.execute(f'INSERT INTO appointments VALUES("{_email}", "{name}", "{file}", {copies}, "{size}", "{type}", "{date}", "{time}", "Pending", 0, "{_request_id}")')

        # Notify user via email
        isUserHasSecondaryEmail = self.execute(f"SELECT secondaryEmail FROM userlist WHERE email = '{_email}'").fetchone()
        subject = "[STI College Davao] You requsted an appointment to the faculty."

        if isUserHasSecondaryEmail is not None:
            text = MIMEText(f"""
            Hi, {name}

            Your Appointment was successfully sent to the faculty. Please check your email/outlook from time-to-time for updates regarding with your appointment. Thank you!

            Appointment Details:
            Date Needed: {date}
            Copies: {copies}
            Size: {size}

            Request ID: {_request_id}
            """)
            text["subject"] = subject
            text["From"] = self.Notifications.gmail_user
            text["To"] = _email
            text["Date"] = email.utils.formatdate(localtime=True)
            self.Notifications.send_user_email([_email, isUserHasSecondaryEmail[0]], text.as_string())

		return True


    def set_user_status_variable(self, email, value):
        # Set the statusFilter variable for a user; insert if not found, otherwise update
        if self.execute(f"SELECT * FROM uservariables WHERE email = '{email}'").fetchone():
            self.execute(f"UPDATE uservariables SET statusFilter = '{value}' WHERE email = '{email}'")
            return True
        else:
            self.execute(f'INSERT INTO uservariables VALUES("{email}", "{value}")')

    def get_user_status_variable(self, email):
        # Retrieve the statusFilter for a user
        try:
            statusFilter = self.execute(f"SELECT statusFilter FROM uservariables WHERE email = '{email}'").fetchone()
            if statusFilter is not None:
                return statusFilter[0]  # Return the value of the status filter
            else:
                return False  # User not found or no filter set
        except Exception as e:
            print(e)
            return False