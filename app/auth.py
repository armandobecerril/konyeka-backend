import os
from dotenv import load_dotenv

load_dotenv()

def validate_login(rfc: str, password: str) -> bool:
    demo_rfc = os.getenv("APP_USER_RFC")
    demo_password = os.getenv("APP_USER_PASSWORD")

    return rfc.upper() == demo_rfc.upper() and password == demo_password