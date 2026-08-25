import psycopg
import os

# Try various password combinations
passwords = ['postgres', 'password', 'admin', '123456', 'postgres123', 'postgres@123', 'Postgres123', '']

for pwd in passwords:
    try:
        conn = psycopg.connect(f'postgresql://postgres:{pwd}@localhost:5432/postgres')
        print(f'Connected with password: "{pwd}"')
        conn.close()
        break
    except Exception as e:
        print(f'  Failed with "{pwd}": {e}')