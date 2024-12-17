import json
import time
import random
import re
import requests
import argparse
from urllib.parse import quote
from http.cookiejar import LWPCookieJar
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.common.action_chains import ActionChains

class RTSessionScraper:
    def __init__(self):
        self.base_url = "https://www.rottentomatoes.com"
        self.search_url = f"{self.base_url}/search"
        self.session = requests.Session()
        self.session.cookies = LWPCookieJar()

        # Realistic browser headers
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'DNT': '1',
            'Cache-Control': 'max-age=0',
        }
        self.session.headers.update(self.headers)

    def _get_with_retry(self, url, params=None, max_retries=3, base_delay=5):
        """Make GET request with exponential backoff retry."""
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"Attempt {attempt + 1} failed: {e}")
                print(f"Retrying in {delay:.1f} seconds...")
                time.sleep(delay)

    def _init_session(self):
        """Initialize session."""
        if not hasattr(self, 'session'):
            self.session = requests.Session()
            self.session.cookies = LWPCookieJar()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Connection': 'keep-alive',
            })
        return True

    def _verify_movie_id(self, movie_id):
        """Verify movie exists and get canonical ID."""
        try:
            url = f"{self.base_url}/m/{movie_id}"
            response = self._get_with_retry(url)

            if response and response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                canonical_link = soup.find('link', {'rel': 'canonical'})
                if canonical_link and 'href' in canonical_link.attrs:
                    canonical_url = canonical_link['href']
                    self.verified_movie_id = canonical_url.split('/m/')[-1].strip('/')
                    print(f"Found verified movie ID: {self.verified_movie_id}")
                    return True

                self.verified_movie_id = movie_id
                return True

            print(f"Movie page not found for ID: {movie_id}")
            return False

        except Exception as e:
            print(f"Error verifying movie ID: {e}")
            return False

    def _search_movie(self, movie_name):
        """Search for a movie and return its RT URL."""
        try:
            search_query = quote(movie_name)
            url = f"{self.search_url}?search={search_query}"

            # Add random delay before search
            time.sleep(random.uniform(1, 2))

            # Update headers specifically for search
            self.session.headers.update({
                'Referer': self.base_url,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            })

            response = self._get_with_retry(url)
            if not response or response.status_code != 200:
                print(f"Failed to access search page. Status code: {response.status_code if response else 'No response'}")
                return None

            soup = BeautifulSoup(response.text, 'html.parser')

            # Try multiple selector patterns
            search_patterns = [
                'search-page-media-row',
                '[data-qa="search-page-media-row"]',
                '.mb-movie',
                '.search__movie-wrap'
            ]

            for pattern in search_patterns:
                search_results = soup.select(pattern)
                if search_results:
                    print(f"Found {len(search_results)} results using pattern: {pattern}")
                    break

            if not search_results:
                print("No search results found with any pattern")
                return None

            # Try to find the movie in results
            for result in search_results:
                # Try multiple title selector patterns
                title_patterns = [
                    '[data-qa="search-page-media-row-movie-title"]',
                    '.movieTitle',
                    '.search-page-media-row__movie-title',
                    'a[href*="/m/"]'
                ]

                for title_pattern in title_patterns:
                    title_elem = result.select_one(title_pattern)
                    if title_elem:
                        title = title_elem.text.strip()
                        print(f"Found movie: {title}")

                        # Check for exact or close match
                        if title.lower() == movie_name.lower() or movie_name.lower() in title.lower():
                            link = title_elem.get('href')
                            if link and '/m/' in link:
                                movie_id = link.split('/m/')[-1].strip('/')
                                print(f"Found matching movie: {title} (ID: {movie_id})")
                                return movie_id
                        break  # Found a title element, no need to try other patterns

            # If no exact match, use first result
            if search_results:
                first_result = search_results[0]
                for title_pattern in title_patterns:
                    title_elem = first_result.select_one(title_pattern)
                    if title_elem:
                        link = title_elem.get('href')
                        if link and '/m/' in link:
                            movie_id = link.split('/m/')[-1].strip('/')
                            print(f"Using first result: {title_elem.text.strip()} (ID: {movie_id})")
                            return movie_id

            print(f"Could not find movie: {movie_name}")
            return None

        except Exception as e:
            print(f"Error searching for movie: {e}")
            print(f"Response content: {response.text if response else 'No response'}")
            return None

    def get_reviews(self, movie_name, num_reviews=100):
        """Get reviews for a movie."""
        try:
            # Search for the movie
            movie_id = self._search_movie(movie_name)
            if not movie_id:
                print(f"Could not find movie: {movie_name}")
                return []

            # Verify movie ID
            if not self._verify_movie_id(movie_id):
                print(f"Invalid movie ID: {movie_id}")
                return []

            print(f"\nFetching reviews for movie: {movie_name} (ID: {movie_id})")

            # Construct the reviews URL
            base_url = f"https://www.rottentomatoes.com/m/{movie_id}/reviews"

            # Use Selenium to get reviews with Load More button functionality
            reviews = self._get_reviews_with_selenium(base_url, num_reviews)

            # Save reviews to JSON file
            output_file = f"{movie_name.lower().replace(' ', '_')}_reviews.json"
            review_data = {
                "movie_name": movie_name,
                "movie_id": movie_id,
                "total_reviews": len(reviews),
                "reviews": reviews
            }

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(review_data, f, indent=2, ensure_ascii=False)
            print(f"\nSaved {len(reviews)} reviews to {output_file}")

            return reviews

        except Exception as e:
            print(f"Error getting reviews: {e}")
            return []
        print(f"Found {len(reviews)} unique reviews")
        return reviews[:num_reviews]

    def _extract_reviews_from_api(self, movie_id, cursor=None):
        """Extract reviews using GraphQL API."""
        try:
            # Use the audience reviews endpoint
            url = f"https://www.rottentomatoes.com/napi/movie/{movie_id}/reviews/audience"
            params = {
                'after': cursor if cursor else '',
                'pageSize': '100',
                'order': 'default'
            }

            print(f"Trying API endpoint: {url}")
            response = self._get_with_retry(url, params=params)

            if response and response.status_code == 200:
                print(f"Successful response from {url}")
                data = response.json()
                reviews = []
                review_data = data.get('reviews', [])
                print(f"Found {len(review_data)} reviews in API response")

                for review in review_data:
                    review_text = review.get('review', '').strip()
                    if review_text:  # Only include reviews with text
                        reviews.append({
                            'text': review_text,
                            'rating': f"{float(review.get('rating', 0))/2:.1f}/5",
                            'author': review.get('authorName', ''),
                            'date': review.get('submissionDate', ''),
                            'source': 'API'
                        })

                next_cursor = data.get('pageInfo', {}).get('endCursor', '')
                has_next = data.get('pageInfo', {}).get('hasNextPage', False)
                return reviews, next_cursor if has_next else None

            print(f"API request failed with status code: {response.status_code if response else 'No response'}")
            return [], None
        except Exception as e:
            print(f"Error extracting reviews from API: {e}")
            return [], None

    def _get_reviews_with_selenium(self, url, num_reviews=100):
        """Get reviews using Selenium with proper button clicking."""
        reviews = []
        seen_reviews = set()

        try:
            # Set up Chrome options
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-extensions')

            # Initialize driver with longer page load timeout
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(30)
            driver.get(url)
            time.sleep(3)  # Initial load wait

            print(f"\nCollecting reviews for movie (target: {num_reviews})")

            # Create WebDriverWait object for explicit waits
            wait = WebDriverWait(driver, 10)

            while len(reviews) < num_reviews:
                # Extract current reviews
                page_reviews = self._extract_reviews_from_html(driver.page_source)
                print(f"\nFound {len(page_reviews)} potential review containers")

                # Process new reviews
                for review in page_reviews:
                    review_key = f"{review.get('author', '')}:{review.get('date', '')}"
                    if review_key and review_key not in seen_reviews:
                        seen_reviews.add(review_key)
                        reviews.append(review)
                        print(f"Found review {len(reviews)}/{num_reviews} with author: {review.get('author', 'Unknown')}, "
                              f"publication: {review.get('publication', 'Unknown')}, rating: {review.get('rating', 'None')}")

                if len(reviews) >= num_reviews:
                    break

                try:
                    # Save page source for debugging
                    with open('debug_page.html', 'w', encoding='utf-8') as f:
                        f.write(driver.page_source)
                    print("\nSaved page source to debug_page.html")

                    # Scroll to bottom
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(2)

                    # Debug: Print all buttons and their text
                    print("\nListing all buttons on page:")
                    buttons = driver.find_elements(By.TAG_NAME, 'button')
                    for idx, button in enumerate(buttons):
                        try:
                            print(f"Button {idx}: Text='{button.text}', Class='{button.get_attribute('class')}', ID='{button.get_attribute('id')}'")
                        except:
                            print(f"Button {idx}: <failed to get attributes>")

                    # Try multiple button detection strategies
                    button_found = False
                    load_more_button = None

                    # Strategy 1: By text content (case insensitive)
                    if not button_found:
                        for button in buttons:
                            try:
                                button_text = button.text.lower()
                                if 'load more' in button_text or 'show more' in button_text:
                                    load_more_button = button
                                    print(f"Found button by text: '{button_text}'")
                                    button_found = True
                                    break
                            except:
                                continue

                    # Strategy 2: By class containing 'load-more'
                    if not button_found:
                        try:
                            load_more_button = driver.find_element(By.CSS_SELECTOR, '[class*="load-more"]')
                            print("Found button by class containing 'load-more'")
                            button_found = True
                        except:
                            print("Button not found by class")

                    # Strategy 3: By aria-label
                    if not button_found:
                        try:
                            load_more_button = driver.find_element(By.CSS_SELECTOR, '[aria-label*="Load"] button')
                            print("Found button by aria-label")
                            button_found = True
                        except:
                            print("Button not found by aria-label")

                    if button_found and load_more_button:
                        try:
                            # Scroll to button
                            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", load_more_button)
                            time.sleep(2)

                            # Try clicking
                            try:
                                load_more_button.click()
                                print("Clicked button using Selenium click")
                            except:
                                driver.execute_script("arguments[0].click();", load_more_button)
                                print("Clicked button using JavaScript click")

                            # Wait for new content
                            time.sleep(3)
                            new_reviews = self._extract_reviews_from_html(driver.page_source)
                            if len(new_reviews) > len(page_reviews):
                                print(f"Successfully loaded more reviews: {len(new_reviews)} > {len(page_reviews)}")
                            else:
                                print("No new reviews loaded after clicking")
                                break
                        except Exception as e:
                            print(f"Error clicking button: {str(e)}")
                            break
                    else:
                        print("Could not find Load More button using any strategy")
                        break

                except Exception as e:
                    print(f"Error during review loading: {str(e)}")
                    break

        except Exception as e:
            print(f"Error during review fetching: {str(e)}")
        finally:
            if 'driver' in locals():
                driver.quit()

        return reviews[:num_reviews]

    def _extract_reviews_from_html(self, html_content):
        """Extract reviews from HTML content with improved parsing."""
        reviews = []
        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Find review containers by data-qa attribute
            review_containers = soup.find_all('div', attrs={'data-qa': 'review-item'})
            print(f"Found {len(review_containers)} potential review containers")

            for container in review_containers:
                try:
                    # Get review text using data-qa
                    text_elem = container.find('p', attrs={'data-qa': 'review-quote'})
                    if not text_elem:
                        continue

                    text = text_elem.get_text().strip()
                    if not text or len(text) < 10:
                        continue

                    # Get author using data-qa
                    author = None
                    author_elem = container.find('a', attrs={'data-qa': 'review-critic-link'})
                    if author_elem:
                        author = author_elem.get_text().strip()

                    # Get publication using data-qa
                    publication = None
                    pub_elem = container.find('a', attrs={'data-qa': 'review-publication'})
                    if pub_elem:
                        publication = pub_elem.get_text().strip()

                    # Get date using data-qa
                    date = None
                    date_elem = container.find('span', attrs={'data-qa': 'review-date'})
                    if date_elem:
                        date = date_elem.get_text().strip()

                    # Get rating from original-score-and-url
                    rating = None
                    score_elem = container.find('p', class_='original-score-and-url')
                    if score_elem:
                        score_text = score_elem.get_text()
                        score_match = re.search(r'Original Score:\s*(\d+(?:\.\d+)?)/(\d+)', score_text)
                        if score_match:
                            num, den = map(float, score_match.groups())
                            rating = f"{(num/den * 5):.1f}/5"

                    # Only add review if it has meaningful content
                    if text and len(text) > 10:
                        review = {
                            'text': text,
                            # 'rating': rating,
                            'author': author,
                            # 'publication': publication,
                            # 'date': date
                        }
                        reviews.append(review)

                except Exception as e:
                    print(f"Error processing individual review: {e}")
                    continue

        except Exception as e:
            print(f"Error extracting reviews from HTML: {e}")

        return reviews

    def _extract_reviews_from_main_page(self, soup):
        """Extract reviews from the main movie page."""
        reviews = []
        try:
            # Try multiple review container selectors specific to main page
            review_sections = [
                {'container': '.audience-reviews__item', 'text': '.audience-reviews__review', 'rating': '.star-display', 'author': '.audience-reviews__name', 'date': '.audience-reviews__duration'},
                {'container': 'review-row', 'text': '.review__text', 'rating': '.review__score', 'author': '.review__name', 'date': '.review__date'},
                {'container': '[data-qa="review-item"]', 'text': '[data-qa="review-text"]', 'rating': '[data-qa="review-score"]', 'author': '[data-qa="review-name"]', 'date': '[data-qa="review-date"]'},
                {'container': '.review_table_row', 'text': '.the_review', 'rating': '.rating', 'author': '.critic-name', 'date': '.review-date'},
            ]

            for section in review_sections:
                containers = soup.select(section['container'])
                for container in containers:
                    text_elem = container.select_one(section['text'])
                    rating_elem = container.select_one(section['rating'])
                    author_elem = container.select_one(section['author'])
                    date_elem = container.select_one(section['date'])

                    text = text_elem.get_text().strip() if text_elem else ""
                    rating = rating_elem.get_text().strip() if rating_elem else ""
                    author = author_elem.get_text().strip() if author_elem else ""
                    date = date_elem.get_text().strip() if date_elem else ""

                    # Convert rating to X/5 format
                    try:
                        if rating:
                            if '%' in rating:
                                rating_num = float(rating.strip('%')) / 20
                                rating = f"{rating_num:.1f}/5"
                            elif '★' in rating:
                                rating_num = rating.count('★')
                                rating = f"{rating_num}/5"
                            elif '/' in rating:
                                parts = rating.split('/')
                                if len(parts) == 2:
                                    try:
                                        num, denom = map(float, parts)
                                        rating = f"{(num/denom * 5):.1f}/5"
                                    except (ValueError, ZeroDivisionError):
                                        rating = ""
                    except Exception as e:
                        print(f"Error converting rating: {e}")
                        rating = ""

                    if text and len(text) > 10:  # Ensure it's a real review
                        reviews.append({
                            'text': text,
                            'rating': rating,
                            'author': author,
                            'date': date
                        })

        except Exception as e:
            print(f"Error extracting reviews from main page: {e}")

        return reviews

def main():
    """Main function to run the scraper from command line."""
    parser = argparse.ArgumentParser(description='Scrape movie reviews from Rotten Tomatoes')
    parser.add_argument('movie_name', type=str, help='Name of the movie to scrape reviews for')
    parser.add_argument('--num_reviews', type=int, default=100, help='Number of reviews to scrape (default: 100)')
    parser.add_argument('--output', type=str, help='Output JSON file name (optional)')

    args = parser.parse_args()

    scraper = RTSessionScraper()
    movie_id = scraper._search_movie(args.movie_name)

    if movie_id:
        output_file = args.output or f"{args.movie_name.lower().replace(' ', '_')}_reviews.json"
        reviews = scraper.get_reviews(movie_id, args.num_reviews)

        output_data = {
            "movie_name": args.movie_name,
            "movie_id": movie_id,
            "total_reviews": len(reviews),
            "reviews": reviews
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"\nReviews saved to: {output_file}")
    else:
        print(f"Failed to find movie: {args.movie_name}")

if __name__ == '__main__':
    main()
