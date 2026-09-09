from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from apscheduler.schedulers.background import BackgroundScheduler
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
login_manager = LoginManager()
scheduler = BackgroundScheduler()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
