def format_user_data(username, email):
    """
    Format user information into a standardized dictionary.
    Useful for logging and database insertion.
    """
    return {
        "user": username.lower(),
        "contact": email.strip(),
        "status": "active"
    }

def get_db_connection_string(host, port):
    """Generates the connection string for PostgreSQL."""
    return f"postgresql://user:pass@{host}:{port}/db"
