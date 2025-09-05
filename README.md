# WhatsUPTurbo 
**Run WhatsApp Web campaigns directly from your device. simple, fast, and free.**

## Why WhatsUPTurbo?  

If you want to send messages from **Your own machine** without SaaS fees or privacy headaches,  
WhatsUPTurbo drives **WhatsApp Web** in Firefox with a persistent profile — sending text and media fast and reliably.

## Features  

- **One-time login** — Reuse your Firefox profile (`whatsup_profile`), scan QR once, stay logged in.  
- **Send anything** — Images, video, docs, audio (up to **50 MB per file**).  
- **Captions** — Supports Arabic (RTL) and English (LTR), including multiline text.  
- **Artifacts** — Every run stores `summary.txt`, `config.json`, `sent_numbers.txt`, `failed.txt`.  
- **Easy CLI** — Simple commands: add `--numbers`, `--numbers-file`, `--message`, `--file`, `--caption`.  

<br>

> ⚠️ **Note**: Numbers are validated in **KSA format** by default (start with `966`, 12 digits).  

<br>

## Quick Start  

#### Install dependencies  
```bash
python3 -m pip install --upgrade pip --break-system-packages
pip3 install selenium webdriver-manager colorama --break-system-packages
```

#### Step 1 – First Run (create profile)  
```bash
python3 WhatsUPTurbo.py --numbers 966XXXXXXXXX --message "Hello"
```
- Firefox opens with profile `whatsup_profile`.  
- Scan the QR code once.  
- Tool confirms login and sends your message.  

#### Step 2 – Send to a list of numbers  
```bash
python3 WhatsUPTurbo.py --numbers-file numbers.txt --message "Promo starts today"
```

#### Step 3 – Send media with caption  
```bash
python3 WhatsUPTurbo.py --numbers-file numbers.txt --file Video.mp4 --caption @caption.txt
```



