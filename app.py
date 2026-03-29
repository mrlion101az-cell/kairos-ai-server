from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return "Kairos AI Server is running"

@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    message = data.get("message", "")

    response = f"Kairos received: {message}"

    return jsonify({"response": response})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
