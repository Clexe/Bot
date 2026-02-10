import json
import psycopg2

# 🔴 PASTE YOUR COPIED URL INSIDE THE QUOTES BELOW:
DATABASE_URL = "PASTE_YOUR_EXTERNAL_DATABASE_URL_HERE"

def migrate():
    try:
        print("🔌 Connecting to Database...")
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        # 1. Create the table immediately
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                settings JSONB DEFAULT '{}'::jsonb,
                is_active BOOLEAN DEFAULT TRUE,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        conn.commit()
        print("✅ Table Created.")

        # 2. Load your local file
        print("📂 Reading users.json...")
        with open('users.json', 'r') as f:
            user_ids = json.load(f)
            
        print(f"🚀 Migrating {len(user_ids)} users...")
        
        # 3. Insert everyone
        count = 0
        for uid in user_ids:
            try:
                # We insert with empty settings; the bot handles defaults automatically
                cur.execute("""
                    INSERT INTO users (user_id, settings) 
                    VALUES (%s, '{}')
                    ON CONFLICT (user_id) DO NOTHING;
                """, (uid,))
                count += 1
            except Exception as e:
                print(f"⚠️ Error on {uid}: {e}")
        
        conn.commit()
        cur.close()
        conn.close()
        print(f"🎉 SUCCESS! Uploaded {count} users to the cloud.")

    except Exception as e:
        print(f"❌ Migration Failed: {e}")

if __name__ == "__main__":
    migrate()
