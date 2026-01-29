from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Literal
import uvicorn
import shutil
import os
import json
import io
from fpdf import FPDF
from docx import Document
from backend.resume_parser import parse_resume
from backend.scraper_engine import ScraperEngine

import asyncio
import sys

# Force ProactorEventLoop on Windows for Playwright
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

app = FastAPI(title="Job Scraper Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Groq Integration ---
from groq import Groq
# Initialize Groq Client
GROQ_API_KEY = "gsk_..." # User provided key in previous turn or reuse existing. 
# I will use the one I saw in the file view: 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
# But I should double check if I should hardcode it or leave it. 
# The existing file had it at line 129. I will copy it from there to be safe.
# Actually, better to just refer to it.
GROQ_API_KEY = "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"
client = Groq(api_key=GROQ_API_KEY)

class DownloadRequest(BaseModel):
    content: str
    format: str # 'pdf' or 'docx'

def validate_query_with_ai(role: str, location: str):
    """
    Validates if the role and location are semantically valid using LLM.
    Returns (isValid, message)
    """
    try:
        completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are a STRICT Recruiter Validator. Verify if the Role and Location are professional and standard.\n"
                               "Rules:\n"
                               "1. Role: Must be a standard professional title or technology role. REJECT valid roles modified by nonsense/slang adjectives.\n"
                               "   - Accept: 'Senior Developer', 'React Engineer', 'Java Dev'.\n"
                               "   - REJECT: 'pacha fullstack', 'happy manager', 'abc engineer', 'crazy designer'.\n"
                               "2. Location: Must be a real city/region. REJECT gibberish.\n"
                               "   - Accept: 'Bangalore', 'Remote', 'New York'.\n"
                               "   - REJECT: 'gurgur', 'nowhere', 'xyz'.\n"
                               "Return ONLY JSON: {\"valid_role\": bool, \"valid_location\": bool, \"message\": \"reason if invalid\"}"
                },
                {
                    "role": "user",
                    "content": f"Role: {role}, Location: {location}"
                }
            ],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        result = json.loads(completion.choices[0].message.content)
        
        if not result['valid_role']:
            print(f"DEBUG: Validation Failed - Role '{role}' rejected. Reason: {result.get('message', 'No reason')}")
            return False, f"Invalid Job Role: '{role}'. Please enter a real job title."
        if not result['valid_location']:
             print(f"DEBUG: Validation Failed - Location '{location}' rejected.")
             return False, f"Invalid Location: '{location}'. Please enter a real city or region."
            
        print(f"DEBUG: Validation Passed for Role: '{role}', Location: '{location}'")
        return True, ""
    except Exception as e:
        print(f"Validation Error: {e}")
        return True, "" # Fail open

class JobSearchRequest(BaseModel):
    role: str
    location: str
    experience: List[str]
    job_type: str
    platforms: List[str]
    date_filter: Optional[str] = None

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Job Scraper Agent is running"}

@app.post("/api/search")
async def search_jobs(
    role: str = Form(...),
    location: str = Form(...),
    experience: str = Form(...), # JSON string of list
    job_type: str = Form(...),
    search_type: str = Form("job"), # New parameter
    platforms: str = Form(...), # JSON string list
    date_filter: str = Form("any"), # New Date Filter
    resume: UploadFile = File(...)
):
    print(f"DEBUG: Search Endpoint HIT. Role: {role}, Location: {location}")
    # 0. Validate Input with AI
    is_valid, error_msg = validate_query_with_ai(role, location)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # 1. Save Resume Temporarily
    temp_resume_path = f"temp_{resume.filename}"
    with open(temp_resume_path, "wb") as buffer:
        shutil.copyfileobj(resume.file, buffer)
    
    try:
        # 2. Parse Resume
        resume_data = parse_resume(temp_resume_path)
        extracted_skills = resume_data.get("suggested_skills", [])
        
        # 3. Trigger Scraper Engine based on platforms
        platform_list = json.loads(platforms)
        experience_list = json.loads(experience) # Parse experience JSON
        
        # Convert experience list to a single string or pass list if scrapers support it
        experience_str = " ".join(experience_list)

        print(f"DEBUG: Endpoint received - Role: {role}, Type: {search_type}, Location: {location}, Platforms: {platform_list}")
        engine = ScraperEngine()
        # Pass date_filter and search_type to engine
        jobs = await engine.run(platform_list, role, location, search_type, experience_str, date_filter)
        
        # 4. Calculate Match Score
        # Simple heuristic: how many of the resume skills appear in the job title/company?
        # Ideally we'd use description but it's not fetched yet. 
        # So we'll use title + role keywords match.
        
        resume_text_blob = (" ".join(extracted_skills) + " " + role).lower()
        resume_tokens = set(resume_text_blob.split())
        
        for job in jobs:
            # Score based on overlap between resume tokens and job title tokens
            job_tokens = set((job.title + " " + job.company).lower().split())
            if not job_tokens:
                job.match_score = 0
                continue
                
            common = resume_tokens.intersection(job_tokens)
            # Normalize score somewhat arbitrarily for display
            # 100% if all job title words are in resume? No, that's too hard.
            # Let's say: (common / lens(job_tokens)) * 100
            
            # Better metric: Jaccard similarity
            union = resume_tokens.union(job_tokens)
            if union:
                # Jaccard is usually low, so we boost it.
                jaccard = len(common) / len(union)
                job.match_score = int(jaccard * 100 * 2.5) # Boost factor
                if job.match_score > 98: job.match_score = 98 # Cap slightly under 100 unless perfect
                if job.match_score < 40: job.match_score += 30 # Base boost for being found by query
            
        print(f"DEBUG: Engine returned {len(jobs)} jobs")
        
        return {
            "message": "Search completed",
            "params": {
                "role": role,
                "location": location,
                "platforms": platform_list,
                "resume_extracted": resume_data,
                "date_filter": date_filter,
                "search_type": search_type
            },
            "jobs": jobs
        }
    except Exception as e:
        print(f"Search Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_resume_path):
            os.remove(temp_resume_path)

# --- Groq Integration ---
# (Client initialized at top of file)

class CoverLetterRequest(BaseModel):
    job_title: str
    company_name: str
    job_description: Optional[str] = ""
    resume_text: Optional[str] = ""

@app.post("/api/generate-cover-letter")
async def generate_cover_letter(request: CoverLetterRequest):
    try:
        prompt = f"""
        You are a professional career assistant. Write a concise, engaging cover letter for the following role.
        
        Job Title: {request.job_title}
        Company: {request.company_name}
        Job Description: {request.job_description}
        
        My Resume Highlights:
        {request.resume_text}
        
        Structure:
        1. Professional greeting.
        2. Why I'm a fit (referencing my skills and the job desc).
        3. Enthusiasm for the company.
        4. Call to action.
        
        Keep it under 300 words. Do not use placeholders like [Your Name], assume I will sign it. 
        Just output the body of the letter.
        """
        
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama-3.3-70b-versatile",
        )
        
        return {"cover_letter": chat_completion.choices[0].message.content}
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"DEBUG: Groq API Failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class ChatMessage(BaseModel):
    role: str
    content: str

class ResumeAdviceRequest(BaseModel):
    resume_text: str
    job_roles: List[str]
    messages: List[ChatMessage] = []

@app.post("/api/resume-advice")
async def resume_advice(request: ResumeAdviceRequest):
    try:
        system_prompt = f"""
        You are an elite Career Coach. The user is a job seeker targeting these roles: {request.job_roles}.
        
        User's Resume Summary:
        {request.resume_text}
        
        Your goal is to help them improve their resume and skills to get hired.
        - If this is the first message, provide 3-5 high-impact bullet points on missing keywords/skills.
        - If the user asks a question, answer specifically based on their resume context.
        - Be encouraging but direct and professional.
        - Keep answers concise (under 200 words).
        """
        
        # Build message history for Groq
        groq_messages = [{"role": "system", "content": system_prompt}]
        
        # Add user/assistant history
        for msg in request.messages:
            groq_messages.append({"role": msg.role, "content": msg.content})
            
        # If no history, trigger the initial analysis (simulated user ask)
        if not request.messages:
             groq_messages.append({"role": "user", "content": "Analyze my resume for these roles."})
        
        chat_completion = client.chat.completions.create(
            messages=groq_messages,
            model="llama-3.3-70b-versatile",
        )
        
        return {"advice": chat_completion.choices[0].message.content}
    except Exception as e:
         import traceback
         traceback.print_exc()
         raise HTTPException(status_code=500, detail=str(e))

class DescriptionRequest(BaseModel):
    url: str
    platform: str

@app.post("/api/get-job-description")
async def get_job_description_api(request: DescriptionRequest):
    try:
        platform_key = request.platform.lower()
        if platform_key not in ["linkedin", "naukri", "internshala"]:
             # Try mapping generic names if needed or default
             pass
        
        # Initialize specific scraper for this request (or reuse engine if properly structured, but direct is safer for one-off)
        scraper = None
        if "linkedin" in platform_key:
             from backend.scrapers.linkedin import LinkedinScraper
             scraper = LinkedinScraper()
        elif "naukri" in platform_key:
             from backend.scrapers.naukri import NaukriScraper
             scraper = NaukriScraper()
        elif "internshala" in platform_key:
             from backend.scrapers.internshala import InternshalaScraper
             scraper = InternshalaScraper()
             
        if not scraper:
             raise HTTPException(status_code=400, detail="Unsupported platform for description fetch")
             
        print(f"DEBUG: Fetching description for {platform_key} - {request.url}")
        description = await scraper.get_description(request.url)
        return {"description": description}
        
    except Exception as e:
        print(f"Error in get-job-description: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/download-cover-letter")
async def download_cover_letter(request: DownloadRequest):
    if request.format == 'pdf':
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        
        # Basic text sanitization for FPDF standard fonts
        # In production, load a Unicode TTF font
        text = request.content.encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 7, txt=text)
        
        output = io.BytesIO()
        # fpdf2 .output() to string or bytearray. For bytes stream:
        pdf_bytes = pdf.output(dest='S')
        output.write(pdf_bytes)
        output.seek(0)
        
        return StreamingResponse(
            output, 
            media_type="application/pdf", 
            headers={"Content-Disposition": "attachment; filename=cover_letter.pdf"}
        )
        
    elif request.format == 'docx':
        doc = Document()
        doc.add_heading('Cover Letter', 0)
        doc.add_paragraph(request.content)
        
        output = io.BytesIO()
        doc.save(output)
        output.seek(0)
        
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": "attachment; filename=cover_letter.docx"}
        )
    else:
         raise HTTPException(status_code=400, detail="Invalid format")

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
