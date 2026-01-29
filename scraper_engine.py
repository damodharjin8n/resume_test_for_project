import asyncio
from typing import List
from .scrapers.base_scraper import JobResult, BaseScraper
from .scrapers.linkedin import LinkedinScraper
from .scrapers.internshala import InternshalaScraper
from .scrapers.naukri import NaukriScraper

class ScraperEngine:
    def __init__(self):
        self.scrapers = {}
        self.scrapers['linkedin'] = LinkedinScraper()
        self.scrapers['internshala'] = InternshalaScraper()
        self.scrapers['naukri'] = NaukriScraper()
        # self.scrapers['indeed'] = IndeedScraper() # TODO

    async def run(self, platforms: List[str], role: str, location: str, search_type: str, experience: str, date_filter: str = None) -> List[JobResult]:
        tasks = []
        
        # Handle "all" case or specific list
        target_platforms = platforms
        if "all" in [p.lower() for p in platforms]:
            target_platforms = self.scrapers.keys()
            
        print(f"Scraping platforms: {target_platforms} for {search_type} with filter: {date_filter}")

        for platform in target_platforms:
            key = platform.lower()
            scraper = self.scrapers.get(key)
            if scraper:
                print(f"Starting scraper for {key}...")
                tasks.append(scraper.search(role, location, search_type, experience, date_filter))
            else:
                print(f"No scraper found for {platform}")
        
        if not tasks:
            return []

        # Run all selected scrapers in parallel
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_jobs = []
        for result in results_list:
            if isinstance(result, list):
                all_jobs.extend(result)
            elif isinstance(result, Exception):
                print(f"Error in scraper task: {result}")
            else:
                print(f"Unexpected result from scraper: {result}")
        
        return all_jobs
