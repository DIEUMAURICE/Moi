from fastapi import FastAPI
import json

app = FastAPI()
DB_FILE = "db.json"

def read_data():
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def write_data(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f)

@app.get("/get")
def get_data():
    return read_data()

@app.post("/save")
def save_data(data: dict):
    write_data(data)
    return {"status": "saved"}