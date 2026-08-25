import psycopg

for pwd in ['', 'postgres', 'password', 'admin', 'root', '123456', 'postgres123']:
    try:
        conn = psycopg.connect(f'postgresql://postgres:{pwd}@localhost:5432/postgres')
        print(f'Connected with password: "{pwd}"')
        conn.close()
        break
    except Exception as e:
        print(f'  Failed with "{pwd}": {e}')