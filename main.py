from data import Database

import identity.web
import os
import smtplib
import email.utils
import threading

from datetime import datetime
from email.mime.text import MIMEText
from flask import Flask, render_template, request, session, redirect, url_for, send_file, send_from_directory
from flask_classful import FlaskView, route
from flask_session import Session
from validate_email import validate_email
from werkzeug.utils import secure_filename
from waitress import serve
from dotenv import load_dotenv

# Load environment variables from the _keys.env file
load_dotenv("_keys.env")

# Initialize Flask app with template and static file configuration
app = Flask(__name__, template_folder="templates/", static_folder="assets/")

# Set max upload size to 10 MB
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  

# Set folder where uploaded files will be saved
app.config["UPLOAD_FOLDER"] = "uploads/"

# Configure session to be permanent and stored in the filesystem
app.config["SESSION_PERMANENT"] = True
app.config["SESSION_TYPE"] = "filesystem"


app.config["SQLALCHEMY_ENGINE_OPTIONS "] = { 
	"pool_recycle": 280,        # Recycle DB connections after 280 seconds
	"pool_pre_ping": True       # Check if connections are alive before using
}

# Apply session configuration to app
Session(app)

# Support for apps running behind a reverse proxy like nginx
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Make the Auth object globally available in Jinja templates
app.jinja_env.globals.update(Auth=identity.web.Auth)

# Initialize Microsoft identity web Auth object with config from environment variables
auth = identity.web.Auth(
    session=session,
    authority=os.environ.get("AUTHORITY"),         # Microsoft Azure AD authority URL
    client_id=os.environ.get("CLIENT_ID"),         # Azure app client ID
    client_credential=os.environ.get("CLIENT_CREDENTIAL"),  # App secret
)

# Set Flask app's secret key (used for session security)
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY")

# Dictionary to map month names to numerical strings
dateNumbers = {
	"January": "01",
	"February": "02",
	"March": "03",
	"April": "04",
	"May": "05",
	"June": "06",
	"July": "07",
	"August": "08",
	"September": "09",
	"October": "10",
	"November": "11",
	"December": "12"
}

# Default status filter for appointments
filter_status = "Pending"

class PrintManagementSystem(FlaskView):
	route_base = "/"
	db = Database()

	NON_STI_ACCOUNT = 1
	BAD_REQUEST = 2

	NON_FACULTY_ADMINS = ["fandino.358281@davao.sti.edu.ph"]
	print("RUNNING")
	"""
	WEBSITE PAGES

	"""

	@route("/")
	def index(self):
		user = auth.get_user()  # Get the current authenticated user
		if request.method == "GET":
			if not user:
				return redirect(url_for("PrintManagementSystem:login_page"))  # Redirect to login if user not authenticated
			else:
				if self.check_is_sti_account():  # Check if user has a valid STI email
					return redirect(url_for("PrintManagementSystem:home"))  # Proceed to home if valid STI user
				else:
					return redirect(url_for("PrintManagementSystem:error_not_sti"))  # Redirect to error if not STI


	@route("/home")
	def home(self):
		user = auth.get_user()  # Get the current user

		if request.method == "GET":
			if not user:
				return redirect(url_for("PrintManagementSystem:login_page"))  # Require login
			else:
				return redirect(url_for("PrintManagementSystem:appointment_page"))  # Redirect to appointments dashboard


	@route("/login")
	def login_page(self):
		if request.method == "GET":
			user = auth.get_user()  # Check for existing session
			if not user:
				# Render login page and trigger Microsoft login popup
				return render_template("login_page.html", **auth.log_in(
					scopes=["User.Read"],
					redirect_uri=url_for("PrintManagementSystem:auth_response", _external=True),
					prompt="select_account",  # Forces account selection even if user is already logged in
				))
			else:
				return redirect(url_for("PrintManagementSystem:index"))  # If already logged in, go to index


	@route("/listofuserslogged")
	def users_logged(self):
		user = auth.get_user()  # Get the current user
		if request.method == "GET":
			if not user:
				# Redirect to login if not authenticated
				return render_template("login_page.html", **auth.log_in(
					scopes=["User.Read"],
					redirect_uri=url_for("PrintManagementSystem:auth_response", _external=True),
					prompt="select_account",
				))
			else:
				# Only users in NON_FACULTY_ADMINS list are allowed to view this page
				if user['preferred_username'] in self.NON_FACULTY_ADMINS:
					return render_template("users_logged.html", user=user, users=self.db.get_all_students())
				else:
					return redirect(url_for("PrintManagementSystem:index"))  # Block unauthorized access


	@route("/error")
	def error_page(self):
		user = auth.get_user()  # Get the current user
		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))  # Require login
		else:
			if self.check_is_sti_account():  # If user is STI, redirect back to index
				return redirect(url_for("PrintManagementSystem:index"))
			else:
				return render_template("error_page.html")  # Otherwise, show error page


	@route("/error_not_sti_account")
	def error_not_sti(self):
		user = auth.get_user()  # Get the current user
		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))  # Require login
		else:
			if self.check_is_sti_account():  # If STI, no need to stay on error page
				return redirect(url_for("PrintManagementSystem:index"))
			else:
				return render_template("non_sti_page.html")  # Render a special page for non-STI accounts


	@route("/my_appointments")
	def appointment_page(self):
		user = auth.get_user()  # Get the currently authenticated user
		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))  # Redirect to login if user not authenticated
		else:
			if self.check_is_sti_account():  # Ensure user has an STI account
				# Render the main dashboard with appointment data
				return render_template(
					"index.html", 
					user=auth.get_user(),  # Pass user info
					my_appointments=self.db.get_appointments(email=user['preferred_username']),  # Only this user's appointments
					appointments=self.db.get_appointments(),  # All appointments (probably for admin/faculty view)
					isFaculty=self.db.check_user_is_faculty_in_database(user['preferred_username']),  # Check if user is a faculty member
					isPaid=self.db.check_user_paid(user['preferred_username']),  # Check if user has paid
					hasSecondaryEmail=self.db.get_secondary_email(self.db.get_studentId_by_email(user['preferred_username'])),  # Check if user has a backup email
					filterStatus=self.db.get_user_status_variable(user['preferred_username']) or "All"  # Get saved filter (e.g. for appointment status)
				)
			else:
				return redirect(url_for("PrintManagementSystem:error_not_sti"))  # Redirect if not STI email


	@route("/manage_students")
	def manage_students_page(self):
		user = auth.get_user()  # Get the currently authenticated user
		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))  # Redirect to login if not authenticated
		else:
			if self.check_is_sti_account():  # STI email check
				# Render faculty/student management page
				return render_template(
					"paid_page.html", 
					user=auth.get_user(),
					my_appointments=self.db.get_appointments(user['preferred_username']),  # Show user's own appointments
					appointments=self.db.get_appointments(),  # Show all appointments (for management)
					isFaculty=self.db.check_user_is_faculty_in_database(user['preferred_username']),  # Faculty check
					isPaid=self.db.check_user_paid(user['preferred_username'])  # Paid status check
				)
			else:
				return redirect(url_for("PrintManagementSystem:error_not_sti"))  # Redirect non-STI users


	@route("/request_appointment")
	def request_page(self):
		user = auth.get_user()  # Get the currently authenticated user
		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))  # Redirect if not logged in
		else:
			if self.check_is_sti_account():  # Ensure user is from STI
				_isUserPaid = self.db.check_user_paid(user['preferred_username'])  # Check payment status
				if _isUserPaid:
					# Show appointment request form if user has paid
					return render_template("request_page.html", 
						user=auth.get_user(),
						isFaculty=self.db.check_user_is_faculty_in_database(user['preferred_username']),
						isPaid=_isUserPaid)
				else:
					# Redirect to index if user hasn't paid
					return redirect(url_for("PrintManagementSystem:index"))
			else:
				return redirect(url_for("PrintManagementSystem:error_not_sti"))  # Block non-STI users


	@route("/settings")
	def setting_page(self):
		user = auth.get_user()  # Get the currently authenticated user
		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))  # Redirect to login if not authenticated
		else:
			if self.check_is_sti_account():  # Ensure STI user
				# Render settings page with user info and email data
				return render_template("setting_page.html", 
					user=auth.get_user(),
					isFaculty=self.db.check_user_is_faculty_in_database(user['preferred_username']),
					isPaid=self.db.check_user_paid(user['preferred_username']),
					secondaryEmail=self.db.get_secondary_email(self.db.get_studentId_by_email(user['preferred_username']))  # Show secondary email if exists
				)
			else:
				return redirect(url_for("PrintManagementSystem:error_not_sti"))  # Block non-STI users


	"""
	CORE FUNCTIONS

	"""


	@route("/logout")
	def logout(self):
		auth.log_out(url_for("PrintManagementSystem:index"))
		return redirect(url_for("PrintManagementSystem:index"))

	def check_is_sti_account(self):
		try:
			user = auth.get_user()
			userEmail = user["preferred_username"]
			emailDomain = userEmail.split("@")[1].split(".")
			userId = userEmail.split("@")[0].split(".")[1]
			if userEmail.endswith("@davao.sti.edu.ph"):
				return True
			else:
				return False

		except Exception as e:
			return False

	@route("/filter_appointments", methods=["POST", "GET"])
	def filter_appointments(self):
		user = auth.get_user()  # Get the current authenticated user
		if request.method == "POST":
			if user:
				status = request.form.get("status")  # Get the selected filter status from the form
				self.db.set_user_status_variable(user['preferred_username'], status)  # Save the selected filter in DB or session
				return ""
			else:
				return redirect(url_for("PrintManagementSystem:index"))  # Redirect if user not authenticated
		else:
			return redirect(url_for("PrintManagementSystem:index"))  # Redirect on GET request

	@route("/check_paid", methods=["POST", "GET"])
	def check_student_paid(self):
		user = auth.get_user()  # Get the current authenticated user
		if request.method == "POST":
			if user:
				student_id = request.form.get("studentId")  # Get the student ID from the request
				if student_id != "":
					# Validate student ID length
					if len(student_id) != 6 and len(student_id) != 11:
						return "invalid"
					
					# Normalize ID if full format (e.g. "20230020001")
					if len(student_id) == 11:
						student_id = student_id.replace("02000", "")

					# Check database for payment status
					student_paid = self.db.check_student_paid(student_id)
					if student_paid:
						return "isPaid"
					else:
						return "notPaid"
				else:
					return ""  # Return empty string if no student ID
			else:
				return redirect(url_for("PrintManagementSystem:index"))  # Redirect if not authenticated
		else:
			return redirect(url_for("PrintManagementSystem:index"))  # Redirect on GET request

	@route("/auth")
	def auth_response(self):
		try:
			result = auth.complete_log_in(request.args)  # Complete the login process
			if "error" in result:
				return render_template("error_page.html")  # Show error if login fails

			user = auth.get_user()  # Get authenticated user info
			userEmail = user["preferred_username"]
			emailDomain = userEmail.split("@")[1].split(".")  # Extract domain for STI check
			username = user['name']
			userId = userEmail.split("@")[0].split(".")[1]  # Extract unique student ID

			# Verify email domain belongs to STI and register the student
			if "sti" in emailDomain:
				self.db.add_student(userEmail, username, userId)
				return redirect(url_for("PrintManagementSystem:index"))
			else:
				return render_template("non_sti_page.html")  # Show warning for non-STI users

		except Exception as e:
			return redirect(url_for("PrintManagementSystem:index"))  # Fail-safe redirect

	@route('/uploads/<string:name>/<path:filename>', methods=['GET', 'POST'])
	def download(self, filename, name):
		user = auth.get_user()  # Get current user
		if request.method == "GET":
			if not user:
				return redirect(url_for("PrintManagementSystem:login_page"))  # Redirect if not logged in
			else:
				# Build file path and serve the file for download
				file_path = app.config['UPLOAD_FOLDER'] + f"{name}_uploads" + f"/{filename}"
				return send_file(file_path, as_attachment=True)

	def save_file(self, name, file, filename):
		# Save file to the specified folder
		file.save(os.path.join(app.config['UPLOAD_FOLDER'] + name, filename))
		return True

	def create_folder(self, name):
		# Create a folder in the upload directory
		os.mkdir(app.config['UPLOAD_FOLDER'] + name)
		return True


	@route("/add_appointment", methods=["POST", "GET"])
	def add_appointment(self):
		user = auth.get_user()  # Get the authenticated user
		if request.method == "POST":
			if user:
				if "fileInput" in request.files:  # Check if file input is present
					file = request.files['fileInput']
					copiesInput = request.form.get("copiesInput")
					sizeInput = request.form.get("sizeInput")
					typeInput = request.form.get("typeInput")
					dateInput = request.form.get("dateInput")
					timeInput = request.form.get("timeInput")

					userEmail = user["preferred_username"]
					userName = user['name']

					# Format date into YYYY-MM-DD format
					dateYear = dateInput.split(" ")[2]
					dateMonth = dateNumbers[dateInput.split(" ")[1].replace(",", "")]
					dateDay = dateInput.split(" ")[0]
					dateFinalStructure = f"{dateYear}-{dateMonth}-{dateDay}"

					# Convert time to 12-hour format with AM/PM
					timeInput = datetime.strptime(timeInput, "%H:%M").strftime("%I:%M %p").lstrip("0")

					if dateInput == "" or timeInput == "":
						return "Error"  # Missing date/time input
					elif dateInput != "" and timeInput != "":
						filename = secure_filename(file.filename)  # Sanitize file name

						try:
							# Create upload folder for the user if it doesn't exist
							os.mkdir(app.config['UPLOAD_FOLDER'] + f"{user['name']}_uploads")
						except FileExistsError:
							pass  # Folder already exists

						# Save appointment in the database
						self.db.add_appointment(userEmail, userName, filename, sizeInput, copiesInput, typeInput, dateInput, timeInput)

						# Save file in a separate thread
						threading.Thread(target=self.save_file, args=(f"/{user['name']}_uploads", file, filename)).start()

						return "Success"
		else:
			# Redirect GET requests to home
			return redirect(url_for("PrintManagementSystem:index"))

	@route("/update_appointment", methods=["POST", "GET"])
	def update_appointment(self):
		user = auth.get_user()
		if request.method == "POST":
			if user:
				new_status = request.form.get("new_status")  # Get new appointment status
				request_id = request.form.get("request_id")  # Get appointment ID
				_email = request.form.get("email")  # Get email to identify user

				# Update appointment status asynchronously
				threading.Thread(target=self.db.update_appointment, args=(_email, request_id, new_status)).start()

				return "updated"
			else:
				# Redirect if not logged in
				return redirect(url_for("PrintManagementSystem:index"))
		else:
			# Redirect GET requests
			return redirect(url_for("PrintManagementSystem:index"))

	@route("/delete_appointment", methods=["POST", "GET"])
	def delete_appointment(self):
		user = auth.get_user()
		if request.method == "POST":
			if user:
				# Asynchronously delete the appointment
				threading.Thread(target=self.db.delete_appointment, args=(user['preferred_username'], request.form.get("request_id"))).start()

				return "eligible"
			else:
				# Redirect if not logged in
				return redirect(url_for("PrintManagementSystem:index"))
		else:
			return redirect(url_for("PrintManagementSystem:index"))

	@route("/cancel_appointment", methods=["POST", "GET"])
	def cancel_appointment(self):
		user = auth.get_user()
		if request.method == "POST":
			if user:
				# Asynchronously cancel the appointment
				threading.Thread(target=self.db.cancel_appointment, args=(user['preferred_username'], request.form.get("request_id"))).start()

				return "eligible"
			else:
				# Redirect if not logged in
				return redirect(url_for("PrintManagementSystem:index"))
		else:
			return redirect(url_for("PrintManagementSystem:index"))

	
	"""
	API ENDPOINTS

	"""


	@route("/api/sti/get_appointments")
	def get_appointments_api(self):
		try:
			appointments = self.db.get_appointments()
			if appointments:
				return appointments
			else:
				# return "No appointments available."
				pass
		except:
			# return "none"
			pass

	@route("/api/sti/get_file/<studentid>/<filename>")
	def get_file_api(self, studentid, filename):
		uploadDir = "uploads/" + self.db.get_name_by_student_id(studentid) + "_uploads"
		isUserHasUploads = os.path.exists(uploadDir)
		
		if isUserHasUploads:
			if os.path.exists(uploadDir + "/" + filename):
				return send_from_directory("uploads", self.db.get_name_by_student_id(studentid) + "_uploads/" + filename)
			else:
				# return "file don't exist"
				pass
		else:
			# return "User has no files"
			pass

	@route("/api/sti/addadmin/<email>")
	def add_admin_api(self, email):
		user = auth.get_user()
		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))
		else:
			# Restrict access to only superadmins and faculty
			if user['preferred_username'] not in self.NON_FACULTY_ADMINS and not self.db.check_user_is_faculty_in_database(user['preferred_username']):
				# return redirect(url_for("PrintManagementSystem:index"))
				pass

			if self.db.add_admin(email):
				return redirect(url_for("PrintManagementSystem:index"))
			else:
				return "Email doesn't exist."

	@route("/api/sti/removeadmin/<email>")
	def remove_admin_api(self, email):
		user = auth.get_user()
		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))
		else:
			if user['preferred_username'] not in self.NON_FACULTY_ADMINS and not self.db.check_user_is_faculty_in_database(user['preferred_username']):
				# return redirect(url_for("PrintManagementSystem:index"))
				pass

			if self.db.remove_admin(email):
				return redirect(url_for("PrintManagementSystem:index"))
			else:
				return "Email doesn't exist."


	@route("/api/sti/setusersecmail", methods=["POST", "GET"])
	def set_user_secondary_email(self):
		user = auth.get_user()

		if request.method == "POST":
			if not user:
				return redirect(url_for("PrintManagementSystem:login_page"))
			else:
				email = request.form.get("userEmail")
				newEmail = request.form.get("newUserEmail")

				# Validate email syntax and domain by sending verification
				is_valid = validate_email(newEmail, verify=True)
				if not is_valid:
					return "invalid"

				# Update secondary email in database
				if self.db.set_secondary_email(email, newEmail):
					return "eligible"
				else:
					return "not found"
		else:
			return redirect(url_for("PrintManagementSystem:index"))

	@route("/api/sti/setstudentpaid", methods=["POST", "GET"])
	def set_student_as_paid(self):
		user = auth.get_user()
		if request.method == "POST":
			if not user:
				return redirect(url_for("PrintManagementSystem:login_page"))
			else:
				email = request.form.get("userEmail")
				studentId = request.form.get("student_id")

				# Ensure only faculty or superadmins can set student as paid
				if email not in self.NON_FACULTY_ADMINS and not self.db.check_user_is_faculty_in_database(email):
					return redirect(url_for("PrintManagementSystem:index"))

				if self.db.set_student_as_paid(studentId):
					return "eligible"
				else:
					return "not found"
		else:
			return redirect(url_for("PrintManagementSystem:index"))

	@route("/api/sti/setstudentpaid/<email>/<studentId>/no")
	def set_student_as_not_paid(self, email, studentId):
		user = auth.get_user()

		if not user:
			return redirect(url_for("PrintManagementSystem:login_page"))
		else:
			if self.db.set_student_as_not_paid(studentId):
				return redirect(url_for("PrintManagementSystem:index"))
			else:
				return "Email doesn't exist."








# Register and start the app using Waitress

server = PrintManagementSystem()
server.register(app)

if __name__ == "__main__":
	serve(app, host='0.0.0.0', port=8080)
