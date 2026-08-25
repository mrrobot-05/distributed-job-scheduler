import psycopg
import os

username = os.getenv('USERNAME', 'adity')
for pwd in ['', 'postgres', 'password', 'admin']:
    try:
        conn = psycopg.connect(f'postgresql://{username}:{pwd}@localhost:5432/postgres')
        print(f'Connected with user {username}, password: "{pwd}"')
        conn.close()
        break
    except Exception as e:
        print(f'  Failed with user {username}, password "{pwd}": {e}')