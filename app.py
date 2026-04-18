from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, redirect, url_for, session
from authlib.integrations.flask_client import OAuth
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from google import genai
from textblob import TextBlob
import language_tool_python
import fitz
import re
import textstat
import time
import difflib
import sqlite3
import os

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

app = Flask(__name__)
app.secret_key = "secret@123"

oauth = OAuth(app)

google = oauth.register(
    name='google',
    CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID"),
    CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

tool = language_tool_python.LanguageTool('en-US')

def init_db():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        password TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        filename TEXT,
        result TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        filename TEXT,
        result REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS essays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT,
        score INTEGER,
        word_count INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

def grammar_spell_check(text):

    # Spell correction
    blob = TextBlob(text)
    corrected_text = str(blob.correct())

    # Grammar check
    matches = tool.check(text)

    errors = []
    for match in matches:
        errors.append(match.message)

    return corrected_text, errors

def get_diff(text1, text2):
    diff = difflib.ndiff(text1.split(), text2.split())
    result = []

    for word in diff:
        if word.startswith("- "):
            result.append(f"<span style='color:red'>{word}</span>")
        elif word.startswith("+ "):
            result.append(f"<span style='color:green'>{word}</span>")
        else:
            result.append(word)

    return " ".join(result)

def extract_text(file):
    filename = file.filename.lower()

    if filename.endswith(".txt"):
        return file.read().decode("utf-8", errors="ignore")

    elif filename.endswith(".pdf"):
        pdf = fitz.open(stream=file.read(), filetype="pdf")
        text = ""
        for page in pdf:
            text += page.get_text()

        text = text.replace("\n", " ")
        text = " ".join(text.split())
        text = text.encode("ascii", "ignore").decode()

        return text

    return ""

def clean_text(text):
    text = text.lower()
    text = re.sub(r'\W+', ' ', text)
    return text[:10000]

def calculate_similarity(text1, text2):
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf = vectorizer.fit_transform([text1, text2])
    similarity = cosine_similarity(tfidf)[0][1]
    return round(similarity * 100, 2)

def format_ai_text(text):

    # Remove markdown symbols
    text = text.replace("###", "")
    text = text.replace("**", "")
    text = text.replace("---", "")

    # Clean extra spaces
    text = "\n".join(line.strip() for line in text.split("\n") if line.strip())

    return text

def evaluate_essay(text):

    words = text.split()
    word_count = len(words)

    readability_score = textstat.flesch_reading_ease(text)

    if readability_score > 60:
        readability = "Easy"
    elif readability_score > 30:
        readability = "Medium"
    else:
        readability = "Hard"

    score = 0

    # 1. Word count (LESS STRICT)
    if word_count > 800:
        score += 20
    elif word_count > 400:
        score += 15
    else:
        score += 10   # not too harsh

    # 2. Readability (IMPROVED)
    if readability == "Easy":
        score += 25
    elif readability == "Medium":
        score += 20
    else:
        score += 10

    # 3. Sentence structure (FIXED)
    sentences = max(text.count("."), 1)
    avg_len = word_count / sentences

    if 8 <= avg_len <= 22:
        score += 25
    else:
        score += 15

    # 4. Vocabulary richness
    unique_words = len(set(words))
    richness = unique_words / max(word_count, 1)

    if richness > 0.6:
        score += 20
    elif richness > 0.4:
        score += 15
    else:
        score += 10

    # Suggestions
    suggestions = []

    if word_count < 400:
        suggestions.append("Add more content to strengthen your essay")

    if readability == "Hard":
        suggestions.append("Simplify sentence structure")

    if avg_len > 25:
        suggestions.append("Use shorter sentences")

    if richness < 0.5:
        suggestions.append("Improve vocabulary variety")

    if not suggestions:
        suggestions.append("Well written essay")

    score += grammar_score(text)

    return {
        "score": score,
        "word_count": word_count,
        "readability": readability,
        "suggestions": suggestions
    }

def grammar_score(text):
    matches = tool.check(text)
    errors = len(matches)

    if errors < 5:
        return 30
    elif errors < 10:
        return 20
    else:
        return 10    
    
@app.route('/')
def home():
    return render_template('index.html')


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = sqlite3.connect("database.db")
        c = conn.cursor()

        c.execute(
            "SELECT * FROM users WHERE email=? AND password=?",
            (email, password)
        )

        user = c.fetchone()
        conn.close()

        if user:
            session["user"] = {"email": email}
            return redirect("/dashboard")
        else:
            return "Invalid credentials"

    return render_template("login.html")


@app.route('/login/google')
def google_login():
    return google.authorize_redirect(
        url_for('authorize', _external=True)
    )


@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    user_info = token.get('userinfo')

    session["user"] = {
        "name": user_info["name"],
        "email": user_info["email"],
        "picture": user_info["picture"]
    }

    return redirect("/dashboard")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        conn = sqlite3.connect("database.db")
        c = conn.cursor()

        c.execute(
            "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
            (name, email, password)
        )

        conn.commit()
        conn.close()

        print(request.form)

        return redirect("/login")  # 🔥 GO TO LOGIN

    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")
    
    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    c.execute(
    "SELECT id, result FROM documents WHERE user_email=?",
    (session["user"]["email"],)
    )

    docs = c.fetchall()

    total_docs = len(docs)

    if total_docs > 0:
        scores = [float(doc[1]) for doc in docs]
        avg_score = round(sum(scores) / total_docs, 2)
        max_score = max(scores)
        min_score = min(scores)
    else:
        avg_score = max_score = min_score = 0

    conn.close()

    return render_template(
        "dashboard.html",
        documents=docs,
        total_docs=total_docs,
        avg_score=avg_score,
        max_score=max_score,
        min_score=min_score,
        essay_result=None,  
        result=None,    
        theme=session.get("theme", "light"),
        accent=session.get("accent", "blue")
    )

@app.route("/upload", methods=["POST"])
def upload():

    file1 = request.files["file1"]
    file2 = request.files["file2"]

    text1 = extract_text(file1)
    text2 = extract_text(file2)

    text1_clean = clean_text(text1)
    text2_clean = clean_text(text2)

    similarity_percent = calculate_similarity(text1_clean, text2_clean)

    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    c.execute(
    "INSERT INTO documents (user_email, filename, result) VALUES (?, ?, ?)",
    (session["user"]["email"], file1.filename, similarity_percent)
    )

    conn.commit()

    c.execute(
    "SELECT id, filename, result FROM documents WHERE user_email=?",
    (session["user"]["email"],)
    )
    docs = c.fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        documents=docs,
        result=similarity_percent, 
        essay_result = None,
        text1=text1,
        text2=text2,
        active_tab="similarity",
        active_section="dashboard",
        theme=session.get("theme", "light"),
        accent=session.get("accent", "blue")
    )


@app.route("/essay-evaluate", methods=["POST"])
def essay_evaluate():

    # FILE
    if "essay_file" in request.files and request.files["essay_file"].filename != "":
        file = request.files["essay_file"]
        text = extract_text(file)
    else:
        text = request.form.get("essay_text", "")

    text_clean = clean_text(text)

    # Your logic
    result = evaluate_essay(text_clean)

    ai_feedback = get_ai_feedback(text[:2000])  # limit for speed
    ai_feedback = format_ai_text(ai_feedback)
    
    improved_essay = rewrite_essay(text[:2000])

    active_tab="essay"

    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    c.execute(
        "SELECT filename, result FROM documents WHERE user_email=?",
    (session["user"]["email"],)
    )

    docs = c.fetchall()
    conn.close()

    return render_template(
        "dashboard.html",
        essay_result=result,
        result = None,
        ai_feedback=ai_feedback,
        improved_essay=improved_essay,
        active_tab="essay",   
        essay_text=text,   
        active_section="dashboard",
        documents=docs, 
        theme=session.get("theme", "light"),
        accent=session.get("accent", "blue")
    )


def get_ai_feedback(text):
    for i in range(3):   # try 3 times
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"""
                Analyze this essay and give:
                - Grammar feedback
                - Suggestions
                - Improvements

                Essay:
                {text}
                """
            )
            return response.text

        except Exception as e:
            print("Retrying AI feedback...", e)
            time.sleep(2)   # wait 2 sec

    # 🔥 FINAL FALLBACK
    return """AI is temporarily unavailable.
Basic Feedback:
- Improve grammar
- Use better vocabulary
- Avoid repetition
- Strengthen conclusion
"""
    
def rewrite_essay(text):
    for i in range(3):   # try 3 times
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"Rewrite this essay clearly:\n{text[:1500]}"
            )
            return response.text

        except Exception as e:
            print("Retrying rewrite...", e)
            time.sleep(2)

    # 🔥 FINAL FALLBACK
    return text

@app.route("/delete-document/<int:doc_id>", methods=["POST"])
def delete_document(doc_id):

    conn = sqlite3.connect("database.db")
    c = conn.cursor()

    c.execute(
        "DELETE FROM documents WHERE id=? AND user_email=?",
        (doc_id, session["user"]["email"])
    )

    conn.commit()
    conn.close()

    return redirect("/dashboard")

@app.route('/logout')
def logout():
    session.clear()
    return redirect("/")

@app.route("/help")
def help_page():
    return render_template("help.html")

@app.route("/save_settings", methods=["POST"])
def save_settings():
    data = request.json

    session["theme"] = data.get("theme", "light")
    session["accent"] = data.get("accent", "blue")

    return {"status": "saved"}


if __name__ == '__main__':
     try:
        init_db()
    except:
        pass
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))