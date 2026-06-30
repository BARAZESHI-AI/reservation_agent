
import os
import psycopg2
from typing import TypedDict
from langgraph.graph import StateGraph
from openai import OpenAI
from datetime import datetime, timedelta


db_pass = os.getenv("DATABASE_PASS")

conn = psycopg2.connect(f"postgresql://postgres.suqjseiaqbffvdzyfbud:{db_pass}@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require")
cur = conn.cursor()
cur.execute("""SELECT id FROM users WHERE chat_id = %s""", (21458882,))
r = cur.fetchone()
print(r[0])
conn.rollback()




ai_api = os.getenv("AI_API")
client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key=ai_api,
)




class State(TypedDict, total=False):
    question: str
    user_id: int
    conn: any
    which_tool: str
    time_stemp: str
    start_time: str
    end_time: str
    answer: str

def to_stime(now):
    if 30 < now.minute <= 59:
        now = now + timedelta(hours=1)
        now = now.replace(minute=0, second=0, microsecond=0)

    # اگر بین 0 تا 30 بود → بشه 30
    if 0 < now.minute <= 30:
        now = now.replace(minute=30, second=0, microsecond=0)
    # اگر خارج از ساعت کاری بود
    if now.hour >= 17 or now.hour < 8:
        now = now + timedelta(days=1)
        now = now.replace(hour=8, minute=0, second=0, microsecond=0)

    return now



def response(prompt):
    client_response = client.chat.completions.create(
    model="anthropic/claude-opus-4.7",
        messages=[
          {
            "role": "user",
            "content": prompt
          }
        ],  max_tokens=200,
  extra_body={"reasoning": {"enabled": True}}
)
    return client_response.choices[0].message.content




#node1
def decide(state):
    question = state["question"]
    prompt = f'''
            question: {question}
            Which of the following categories does this question belong to?
            1_Reserve time
            2_View reserved time
            3_Cancel reserved time
            4_other
            Just return the number'''
    result = response(prompt)
    state["which_tool"] = result.strip().lower()
    return state






def extract_time(state):
    question = state["question"]

    prompt1 = f"""
Text:
{question}

Task:
Extract ONLY the reservation START datetime.

Rules:
- Return ONLY the start datetime
- Format: YYYY-MM-DD HH:MM:SS
- If multiple dates exist, choose the start of reservation
- If no reservation start datetime exists, return exactly: no
- No explanation
"""

    result1 = response(prompt1).strip()

    # ❌ اگر چیزی پیدا نشد
    if result1 == "no":
        state["time_stemp"] = "no"
        return state

    try:
        dt = datetime.strptime(result1, "%Y-%m-%d %H:%M:%S")

        # 🔥 چک مهم: گذشته نباشه
        now = datetime.now()

        if dt < now:
            state["time_stemp"] = "past"
            return state

        # ✅ معتبره
        state["time_stemp"] = "yes"
        state["start_time"] = result1

        new_dt = dt + timedelta(minutes=30)
        state["end_time"] = new_dt.strftime("%Y-%m-%d %H:%M:%S")

        return state

    except Exception:
        # ❌ اگر فرمت خراب بود
        state["time_stemp"] = "invalid"
        return state


#node2
def reserve_time(state):
    conn = state["conn"]
    conn.rollback()
    state = extract_time(state)

    cur = conn.cursor()

    try:
        # ✅ اگر کاربر زمان داده

        if state["time_stemp"] == "yes":
            st = state["start_time"]
            et = state["end_time"]

            query = """
            SELECT EXISTS (
                SELECT 1 FROM reserv
                WHERE NOT (end_time <= %s OR start_time >= %s)
            )
            """

            cur.execute(query, (st, et))
            exists = cur.fetchone()[0]

            if exists:
                state["answer"] = "can not reserve this time ❌"
                return state

            cur.execute("""
                INSERT INTO reserv (user_id, start_time, end_time)
                VALUES (%s, %s, %s)
            """, (state["user_id"], st, et))

            conn.commit()  # ✅ مهم
            state["answer"]=f"reserved at {st} ✅"
            return state

        # ✅ اگر زمان نداده → پیدا کردن اولین تایم خالی
        elif state["time_stemp"] == "no":
            now = to_stime(datetime.now())
            dt = now

            for _ in range(50):  # جلوگیری از لوپ بی‌نهایت
                query = """
                SELECT EXISTS (
                    SELECT 1 FROM reserv
                    WHERE start_time = %s
                )
                """

                cur.execute(query, (dt,))
                exists = cur.fetchone()[0]

                if not exists:
                    et = dt + timedelta(minutes=30)

                    cur.execute("""
                        INSERT INTO reserv (user_id, start_time, end_time)
                        VALUES (%s, %s, %s)
                    """, (state["user_id"], dt, et))

                    conn.commit()
                    state["answer"]=f"reserved at {dt} ✅"
                    return state

                dt = dt + timedelta(minutes=30)
            state["answer"]="no free time found ❌"
            return state
        elif state["time_stemp"] == "past":
            state["answer"]="this time is past"
            return state
    finally:
        cur.close()





#node3
def get_time_reservd(state):
    conn = state["conn"]
    conn.rollback()
    cur = conn.cursor()
    try:
        conn.rollback()

        cur.execute("""SELECT * FROM reserv WHERE user_id = %s""", (state["user_id"],))
        tr = cur.fetchall()[-1]

        if tr:
            state["answer"] = tr
            return state
        else:
            state["answer"]="you dont have resrv time"
            return state
    finally:
        cur.close()






def cancel_last_reserve(state):
    conn = state["conn"]
    cur = conn.cursor()

    try:
        cur.execute("""
            DELETE FROM reserv
            WHERE id = (
                SELECT id FROM reserv
                WHERE user_id = %s
                ORDER BY id DESC
                LIMIT 1
            )
            RETURNING start_time
        """, (state["user_id"],))

        deleted = cur.fetchone()
        conn.commit()

        if deleted:
            state["answer"]= f"last reservation at {deleted[0]} canceled ✅"
            return state
        else:
            state["answer"]="no reservation found ❌"
            return state

    finally:
        cur.close()

def other(state):
    query = f"""Answer this question:{state["question"]} and say that you can only help with reservation-related actions."""
    res = response(query)
    state["answer"] = res
    return state


def finalli(state):
    prompt = f"""Give the user a nice answer based on the information I give you.information: {state['answer']}"""
    res = response(prompt)
    state["answer"] = res
    return state

def route(state):
    return state["which_tool"]

graph = StateGraph(State)
graph.add_node("decide", decide)
graph.add_node("reserv", reserve_time)
graph.add_node("see",get_time_reservd)
graph.add_node("cancel",cancel_last_reserve)
graph.add_node("other",other)
graph.add_node("final", finalli)

graph.set_entry_point("decide")


graph.add_conditional_edges(
    "decide",
    route,
    {
        "1": "reserv",
        "2": "see",
        "3": "cancel",
        "4": "other",
    }
)

graph.add_edge("reserv","final")
graph.add_edge("see","final")
graph.add_edge("cancel","final")


app = graph.compile()

# اجرا
