# Python නිල ලයිට් වේශය ලබා ගැනීම
FROM python:3.10-slim

# වර්කින් ෆෝල්ඩර් එක සැකසීම
WORKDIR /app

# අවශ්‍ය ලයිබ්‍රරි කොපි කර ඉන්ස්ටෝල් කිරීම
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ඉතිරි සියලුම ෆයිල්ස් කොපි කිරීම
COPY . .

# Koyeb මඟින් ලබාදෙන පෝට් එක හරහා සර්වර් එක ස්ටාර්ට් කිරීම
CMD ["python", "main.py"]
