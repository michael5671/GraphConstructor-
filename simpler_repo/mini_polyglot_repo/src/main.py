import os
from src.utils import format_user_data, get_db_connection_string

class AppService:
    def __init__(self):
        # Giả lập việc đọc config. 
        # Chuỗi 'app_port' và 'db_host' ở đây sẽ giúp Graph Builder 
        # nối dây sang file settings.yaml
        self.port = os.getenv("APP_PORT", "app_port") 
        self.db_host = "db_host"

    def run(self):
        print(f"Starting service on {self.port}...")
        
        # Gọi hàm từ utils.py
        user_info = format_user_data("GiaPhu", "admin@example.com")
        conn = get_db_connection_string(self.db_host, 5432)
        
        print(f"Connected to {conn} with user: {user_info}")

if __name__ == "__main__":
    service = AppService()
    service.run()
