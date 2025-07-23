# Core imports for Flask, threading, mailing, and authentication
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

# Load environment variables securely
load_dotenv("_keys.env")

# Configure Flask app
app = Flask(__name__, template_folder="templates/", static_folder="assets/")
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # Max 10MB file uploads
app.config["UPLOAD_FOLDER"] = "uploads/"
app.config["SESSION_PERMANENT"] = True
app.config["SESSION_TYPE"] = "filesystem"

# Configure SQLAlchemy connection pooling
app.config["SQLALCHEMY_ENGINE_OPTIONS "] = {
	"pool_recycle" : 280,
	"pool_pre_ping" : True
}
Session(app)

# Support for apps behind proxies (e.g., on Heroku, Azure)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Set up Microsoft identity B2C authentication
app.jinja_env.globals.update(Auth=identity.web.Auth)
auth = identity.web.Auth(
    session=session,
    authority=os.environ.get("AUTHORITY"),
    client_id=os.environ.get("CLIENT_ID"),
    client_credential=os.environ.get("CLIENT_CREDENTIAL"),
)

# Secret key for session encryption
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY")

# Month name to number mapping for date parsing
dateNumbers = {
	"January" : "01",
	"February" : "02",
	"March" : "03",
	"April" : "04",
	"May" : "05",
	"June" : "06",
	"July" : "07",
	"August" : "08",
	"September" : "09",
	"October" : "10",
	"November" : "11",
	"December" : "12"
}

filter_status = "Pending"

class PrintManagementSystem(FlaskView):
	route_base = "/"
	db = Database()

	NON_STI_ACCOUNT = 1
	BAD_REQUEST = 2

	# List of users that are allowed to access restricted admin views
	NON_FACULTY_ADMINS = ["fandino.358281@davao.sti.edu.ph"]

	print("RUNNING")

	"""
	WEBSITE PAGES
	"""

	@route("/")
	def index(self):
		user = auth.get_user()
		if request.method == "GET":
			# Redirect to login if not authenticated
			if not user:
				return redirect(url_for("PrintManagementSystem:login_page"))
			else:
				# STI users go to home, others go to error page
				if self.check_is_sti_account():
					return redirect(url_for("PrintManagementSystem:home"))
				else:
					return redirect(url_for("PrintManagementSystem:error_not_sti"))

	@route("/home")
	def home(self):
		user = auth.get_user()
		if request.method == "GET":
			if not user:
				return redirect(url_for("PrintManagementSystem:login_page"))
			else:
				# Redirect to main appointment interface
				return redirect(url_for("PrintManagementSystem:appointment_page"))

	@route("/login")
	def login_page(self):
		if request.method == "GET":
			user = auth.get_user()
			if not user:
				# Render Microsoft login
				return render_template("login_page.html", **auth.log_in(
				        scopes=["User.Read"],
				        redirect_uri=url_for("PrintManagementSystem:auth_response", _external=True),
				        prompt="select_account",
				        ))
			else:
				return redirect(url_for("PrintManagementSystem:index"))

	@route("/users_logged")
	def users_logged(self):
		user = auth.get_user()
		if request.method == "GET":
			if not user:
				return render_template("login_page.html", **auth.log_in(
				        scopes=["User.Read"],
				        redirect_uri=url_for("PrintManagementSystem:auth_response", _external=True),
				        prompt="select_account",
				        ))
			else:
				# Only admin users can see list of logged users
				if user['preferred_username'] in self.NON_FACULTY_ADMINS:
					return render_template("users_logged.html", user=user, users=self.db.get_all_students())
				else:
					return redirect(url_for("PrintManagementSystem:index"))

	# ... [Other routes follow similar patterns]

	"""
	CORE FUNCTIONS
	"""

	@route("/logout")
	def logout(self):
		# End user session and redirect to index
		auth.log_out(url_for("PrintManagementSystem:index"))
		return redirect(url_for("PrintManagementSystem:index"))

	def check_is_sti_account(self):
		# Check if the authenticated user is from the STI domain
		try:
			user = auth.get_user()
			userEmail = user["preferred_username"]
			if userEmail.endswith("@davao.sti.edu.ph"):
				return True
			else:
				return False
		except Exception as e:
			return False

	@route("/add_appointment", methods=["POST", "GET"])
	def add_appointment(self):
		user = auth.get_user()
		if request.method == "POST":
			if user:
				# Handle file upload and appointment form submission
				if "fileInput" in request.files:
					file = request.files['fileInput']
					# Collect metadata
					copiesInput = request.form.get("copiesInput")
					sizeInput = request.form.get("sizeInput")
					typeInput = request.form.get("typeInput")
					dateInput = request.form.get("dateInput")
					timeInput = request.form.get("timeInput")

					# Convert date string to standard format
					dateYear = dateInput.split(" ")[2]
					dateMonth = dateNumbers[dateInput.split(" ")[1].replace(",","")]
					dateDay = dateInput.split(" ")[0]
					dateFinalStructure = f"{dateYear}-{dateMonth}-{dateDay}"

					timeInput = datetime.strptime(timeInput, "%H:%M").strftime("%I:%M %p").lstrip("0")
					if dateInput == "" or timeInput == "":
						return "Error"

					# Save file securely
					filename = secure_filename(file.filename)
					try:
						os.mkdir(app.config['UPLOAD_FOLDER'] + f"{user['name']}_uploads")
					except FileExistsError:
						pass  # Ignore if folder already exists

					self.db.add_appointment(user["preferred_username"], user['name'], filename, sizeInput, copiesInput, typeInput, dateInput, timeInput)

					# Save the file in a background thread
					threading.Thread(target=self.save_file, args=(f"/{user['name']}_uploads", file, filename)).start()

					return "Success"
		else:
			return redirect(url_for("PrintManagementSystem:index"))

	"""
	API ENDPOINTS
	"""

	@route("/api/sti/get_file/<studentid>/<filename>")
	def get_file_api(self, studentid, filename):
		# Serve user file based on student ID
		uploadDir = "uploads/" + self.db.get_name_by_student_id(studentid) + "_uploads"
		isUserHasUploads = os.path.exists(uploadDir)

		if isUserHasUploads:
			if os.path.exists(uploadDir + "/" + filename):
				return send_from_directory("uploads", self.db.get_name_by_student_id(studentid) + "_uploads/" + filename)
			else:
				return "file don't exist"
		else:
			return "User has no files"

# Register the controller
server = PrintManagementSystem()
server.register(app)

# Run the app with Waitress (for production use)
if __name__ == "__main__":
	serve(app, host='0.0.0.0', port=8080)
