"""
Browser tests for Distributed Job Scheduler using Playwright.
Run with: pytest tests_browser.py -v --headed (to see browser) or headless (default)
"""
import pytest
from playwright.sync_api import Page, expect


@pytest.fixture(scope="session")
def base_url():
    return "http://localhost:8000"


@pytest.fixture(scope="session")
def demo_credentials():
    return {"email": "demo@demo.com", "password": "demo123"}


@pytest.fixture
def logged_in_page(page: Page, base_url: str, demo_credentials: dict):
    """Log in and return the page."""
    page.goto(f"{base_url}/login/")
    page.fill('input[name="username"]', demo_credentials["email"])
    page.fill('input[name="password"]', demo_credentials["password"])
    page.click('button[type="submit"]')
    # Wait for redirect to dashboard
    page.wait_for_url("**/")
    return page


class TestAuthFlow:
    """Test authentication flows."""
    
    def test_login_page_loads(self, page: Page, base_url: str):
        """Test that login page loads correctly."""
        page.goto(f"{base_url}/login/")
        expect(page).to_have_title("Distributed Job Scheduler")
        expect(page.locator('h2')).to_contain_text("Sign in to your account")
        expect(page.locator('form')).to_be_visible()
    
    def test_login_success(self, page: Page, base_url: str, demo_credentials: dict):
        """Test successful login."""
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', demo_credentials["email"])
        page.fill('input[name="password"]', demo_credentials["password"])
        page.click('button[type="submit"]')
        
        # Should redirect to dashboard
        page.wait_for_url("**/")
        expect(page.locator("h1")).to_contain_text("Dashboard")
    
    def test_login_failure(self, page: Page, base_url: str):
        """Test login failure with invalid credentials."""
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "wrong@email.com")
        page.fill('input[name="password"]', "wrongpassword")
        page.click('button[type="submit"]')
        
        # Should show error
        expect(page.locator('.bg-red-50')).to_be_visible()


class TestDashboard:
    """Test dashboard functionality."""
    
    def test_dashboard_loads(self, logged_in_page: Page):
        """Test that dashboard loads with all components."""
        page = logged_in_page
        expect(page.locator("h1")).to_contain_text("Dashboard")
        
        # Check summary cards
        expect(page.locator("#card-workers")).to_be_visible()
        expect(page.locator("#card-queued")).to_be_visible()
        expect(page.locator("#card-running")).to_be_visible()
        expect(page.locator("#card-dlq")).to_be_visible()
        
        # Check charts
        expect(page.locator("#throughput-chart")).to_be_visible()
        expect(page.locator("#status-chart")).to_be_visible()
        expect(page.locator("#queue-chart")).to_be_visible()
        expect(page.locator("#worker-chart")).to_be_visible()
    
    def test_dashboard_refresh(self, logged_in_page: Page):
        """Test dashboard refresh button."""
        page = logged_in_page
        page.click("#refresh-btn")
        # Just verify it doesn't error
        expect(page.locator("#refresh-text")).to_have_text("Refresh")


class TestProjects:
    """Test project management."""
    
    def test_projects_list(self, logged_in_page: Page, base_url: str):
        """Test projects list page."""
        page = logged_in_page
        page.goto(f"{base_url}/projects/")
        expect(page.locator("h1")).to_contain_text("Projects")
        expect(page.locator('a[href="/projects/create/"]')).to_be_visible()
    
    def test_create_project(self, logged_in_page: Page, base_url: str):
        """Test creating a new project."""
        page = logged_in_page
        page.goto(f"{base_url}/projects/create/")
        page.fill('input[name="name"]', "Test Project")
        page.click('button[type="submit"]')
        
        # Should redirect to project list
        page.wait_for_url("**/projects/")
        expect(page.locator("text=Test Project")).to_be_visible()
    
    def test_project_detail(self, logged_in_page: Page, base_url: str):
        """Test project detail page."""
        page = logged_in_page
        page.goto(f"{base_url}/projects/")
        # Click on first project
        page.click('a[href^="/projects/"]:not([href*="create"])')
        expect(page.locator("h1")).to_contain_text("API Project")


class TestQueues:
    """Test queue management."""
    
    def test_queues_list(self, logged_in_page: Page, base_url: str):
        """Test queues list page."""
        page = logged_in_page
        page.goto(f"{base_url}/projects/")
        # Click on queues for first project
        page.click('a[href*="/queues/"]')
        expect(page.locator("h1")).to_contain_text("Queues")
    
    def test_queue_pause_resume(self, logged_in_page: Page, base_url: str):
        """Test pausing and resuming a queue."""
        page = logged_in_page
        page.goto(f"{base_url}/projects/")
        page.click('a[href*="/queues/"]')
        
        # Find pause button for first queue
        pause_btn = page.locator('form[action*="/pause/"] button').first
        if pause_btn.is_visible():
            pause_btn.click()
            expect(page.locator("text=Paused")).to_be_visible()


class TestJobExplorer:
    """Test job explorer functionality."""
    
    def test_job_explorer_loads(self, logged_in_page: Page, base_url: str):
        """Test job explorer page loads."""
        page = logged_in_page
        page.goto(f"{base_url}/jobs/explorer/")
        expect(page.locator("h1")).to_contain_text("Job Explorer")
        expect(page.locator("#jobs-table-body")).to_be_visible()
    
    def test_job_explorer_search(self, logged_in_page: Page):
        """Test filtering jobs."""
        page = logged_in_page
        page.goto(f"{base_url}/jobs/explorer/")
        
        # Filter by status
        page.select_option("#filter-status", "COMPLETED")
        page.click('button:has-text("Search")')
        
        # Wait for results
        page.wait_for_load_state("networkidle")
        expect(page.locator("#jobs-table-body")).to_be_visible()
    
    def test_job_explorer_pagination(self, logged_in_page: Page):
        """Test pagination works."""
        page = logged_in_page
        page.goto(f"{base_url}/jobs/explorer/")
        page.wait_for_load_state("networkidle")
        
        # Check pagination exists if multiple pages
        if page.locator("#pagination nav").is_visible():
            page.click('#pagination button:has-text("Next")')
            page.wait_for_load_state("networkidle")


class TestJobDetail:
    """Test job detail page."""
    
    def test_job_detail_modal(self, logged_in_page: Page, base_url: str):
        """Test job detail modal opens."""
        page = logged_in_page
        page.goto(f"{base_url}/jobs/explorer/")
        page.wait_for_load_state("networkidle")
        
        # Click View on first job
        page.click('button:has-text("View")')
        
        # Modal should open
        expect(page.locator("#job-modal")).to_be_visible()
        expect(page.locator("#modal-job-name")).to_be_visible()


class TestWorkers:
    """Test workers page."""
    
    def test_workers_list(self, logged_in_page: Page, base_url: str):
        """Test workers list page."""
        page = logged_in_page
        page.goto(f"{base_url}/workers/")
        expect(page.locator("h1")).to_contain_text("Workers")
        expect(page.locator("table")).to_be_visible()


class TestScheduledJobs:
    """Test scheduled jobs."""
    
    def test_scheduled_jobs_list(self, logged_in_page: Page, base_url: str):
        """Test scheduled jobs list."""
        page = logged_in_page
        page.goto(f"{base_url}/scheduled/")
        expect(page.locator("h1")).to_contain_text("Scheduled Jobs")


class TestBatchJobs:
    """Test batch jobs page."""
    
    def test_batch_jobs_page(self, logged_in_page: Page, base_url: str):
        """Test batch jobs page."""
        page = logged_in_page
        page.goto(f"{base_url}/jobs/batch/")
        expect(page.locator("h1")).to_contain_text("Batch Jobs")


class TestDLQ:
    """Test Dead Letter Queue."""
    
    def test_dlq_list(self, logged_in_page: Page, base_url: str):
        """Test DLQ page."""
        page = logged_in_page
        page.goto(f"{base_url}/dlq/")
        expect(page.locator("h1")).to_contain_text("Dead Letter Queue")


class TestAPIEndpoints:
    """Test API endpoints directly."""
    
    def test_submit_job_api(self, logged_in_page: Page, base_url: str):
        """Test submitting a job via API."""
        page = logged_in_page
        
        # Use page API to make request
        response = page.request.post(
            f"{base_url}/api/jobs/submit/",
            headers={"X-Project-Key": "demo-api-key-1", "Content-Type": "application/json"},
            data={"name": "test_job", "queue": "default", "payload": {"test": "data"}}
        )
        
        assert response.ok
        data = response.json()
        assert data["status"] == "queued"
        assert "id" in data
    
    def test_list_jobs_api(self, logged_in_page: Page, base_url: str):
        """Test listing jobs via API."""
        page = logged_in_page
        
        response = page.request.get(
            f"{base_url}/api/jobs/",
            headers={"X-Project-Key": "demo-api-key-1"}
        )
        
        assert response.ok
        data = response.json()
        assert "results" in data
        assert "count" in data
    
    def test_stats_api(self, logged_in_page: Page, base_url: str):
        """Test stats API."""
        page = logged_in_page
        
        response = page.request.get(
            f"{base_url}/api/stats/",
            headers={"X-Project-Key": "demo-api-key-1"}
        )
        
        assert response.ok
        data = response.json()
        assert "active_workers" in data
        assert "jobs_queued" in data


class TestResponsive:
    """Test responsive design."""
    
    @pytest.mark.parametrize("viewport", [
        {"width": 375, "height": 667},  # Mobile
        {"width": 768, "height": 1024},  # Tablet
        {"width": 1920, "height": 1080},  # Desktop
    ])
    def test_responsive_dashboard(self, page: Page, base_url: str, demo_credentials: dict, viewport):
        """Test dashboard is responsive."""
        page.set_viewport_size(viewport)
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', demo_credentials["email"])
        page.fill('input[name="password"]', demo_credentials["password"])
        page.click('button[type="submit"]')
        page.wait_for_url("**/")
        
        # Dashboard should be visible
        expect(page.locator("h1")).to_contain_text("Dashboard")


# Run with: pytest tests_browser.py -v --headed (to see browser)
# Or: pytest tests_browser.py -v (headless)
if __name__ == "__main__":
    pytest.main(["-v", "--headed", __file__])