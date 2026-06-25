"""Shared configuration for YC Death Watch."""

from __future__ import annotations

ALGOLIA_APP_ID = "45BWZJ1SGC"
ALGOLIA_API_KEY = (
    "NzllNTY5MzJiZGM2OTY2ZTQwMDEzOTNhYWZiZGRjODlhYzVkNjBmOGRjNzJiMWM4ZTU0"
    "ZDlhYTZjOTJiMjlhMWFuYWx5dGljc1RhZ3M9eWNkYyZyZXN0cmljdEluZGljZXM9WUNDb21w"
    "YW55X3Byb2R1Y3Rpb24lMkNZQ0NvbXBhbnlfQnlfTGF1bmNoX0RhdGVfcHJvZHVjdGlvbiZ0"
    "YWdGaWx0ZXJzPSU1QiUyMnljZGNfcHVibGljJTIyJTVE"
)
ALGOLIA_INDEX = "YCCompany_production"
DEATHBYCLAWD_URL = "https://deathbyclawd.com/.netlify/functions/analyze"

# MVP: last 4 years of batches (Winter 2022 → latest)
MVP_BATCHES = [
    "Winter 2022",
    "Summer 2022",
    "Winter 2023",
    "Summer 2023",
    "Winter 2024",
    "Summer 2024",
    "Fall 2024",
    "Winter 2025",
    "Spring 2025",
    "Summer 2025",
    "Fall 2025",
    "Winter 2026",
    "Spring 2026",
    "Summer 2026",
    "Fall 2026",
    "Winter 2027",
]

# First deploy wave: latest → all 2025 batches (scrape before initial GitHub Pages push)
PRIORITY_BATCHES = [
    "Winter 2027",
    "Fall 2026",
    "Summer 2026",
    "Spring 2026",
    "Winter 2026",
    "Fall 2025",
    "Summer 2025",
    "Spring 2025",
    "Winter 2025",
]

# Second wave: 2022–2024 (run after deploy)
OLDER_BATCHES = [
    "Winter 2022",
    "Summer 2022",
    "Winter 2023",
    "Summer 2023",
    "Winter 2024",
    "Summer 2024",
    "Fall 2024",
]

EXPANSION_BATCHES = [
    "Winter 2021",
    "Summer 2021",
    "Winter 2020",
    "Summer 2020",
    "Winter 2019",
    "Summer 2019",
    "Winter 2018",
    "Summer 2018",
    "Winter 2017",
    "Summer 2017",
    "Winter 2016",
    "Summer 2016",
    "Winter 2015",
    "Summer 2015",
    "Winter 2014",
    "Summer 2014",
    "Winter 2013",
    "Summer 2013",
]

NON_SAAS_INDUSTRIES = {
    "Healthcare",
    "Healthcare IT",
    "Biotech",
    "Hardware",
    "Industrials",
    "Consumer",
    "Consumer Health and Wellness",
    "Consumer Electronics",
    "Food and Beverage",
    "Energy",
    "Climate",
    "Agriculture",
    "Real Estate and Construction",
    "Government",
    "Education",
    "Drones",
    "Robotics",
}

SAAS_LIKELY_INDUSTRIES = {
    "B2B",
    "B2B SaaS",
    "SaaS",
    "Developer Tools",
    "Fintech",
    "Finance",
    "Analytics",
    "Security",
    "Infrastructure",
    "Productivity",
    "Marketing",
    "Sales",
    "Recruiting",
    "Legal",
    "Operations",
    "Engineering, Product and Design",
}

SCRAPE_DELAY_SECONDS = 2.5
SCRAPE_TIMEOUT_SECONDS = 90
SCRAPE_MAX_RETRIES = 3
