import sqlite3
import sys

def export_cookies(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # SQL-запрос БЕЗ комментариев с '#'
    cursor.execute("""
        SELECT 
            host_key,
            CASE 
                WHEN substr(host_key,1,1) = '.' THEN 'TRUE' 
                ELSE 'FALSE' 
            END,
            path,
            CASE 
                WHEN is_secure = 1 THEN 'TRUE' 
                ELSE 'FALSE' 
            END,
            (expires_utc / 1000000) - 11644473600,  -- Конвертация времени (через '--')
            name,
            value
        FROM cookies
    """)

    with open('cookies.txt', 'w') as f:
        f.write("# Netscape HTTP Cookie File\n")
        for row in cursor:
            f.write("\t".join(map(str, row)) + "\n")

    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python3 cookies.py <path-to-chrome-cookies-db>")
        sys.exit(1)
        
    export_cookies(sys.argv[1])
    print("Cookies успешно экспортированы в cookies.txt")
