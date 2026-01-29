import pdfplumber
import re
from typing import Dict, List

def extract_text_from_pdf(file_path: str) -> str:
    text = ""
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

def parse_resume(file_path: str) -> Dict[str, str]:
    text = extract_text_from_pdf(file_path)
    
    # Basic keyword extraction (This is a placeholder for more advanced logic)
    # in a real scenario, we might use NLP or dedicated libraries.
    
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    phone_pattern = r'(\+\d{1,3}[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}'
    
    emails = re.findall(email_pattern, text)
    phones = re.findall(phone_pattern, text)
    
    # Heuristic for skills (very basic)
    common_skills = ["python", "java", "react", "javascript", "sql", "aws", "docker", "kubernetes", "fastapi", "django", "nodejs"]
    found_skills = [skill for skill in common_skills if skill.lower() in text.lower()]
    
    return {
        "text": text,
        "email": emails[0] if emails else None,
        "phone": phones[0] if phones else None,
        "suggested_skills": found_skills
    }
