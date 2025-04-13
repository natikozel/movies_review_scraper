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
from sanitizer import sanitize_reviews_json
from collections import namedtuple
import os

Args = namedtuple('Args', ['movie_name', 'num_reviews', 'output'])

class RTSessionScraper:
    def __init__(self):
        self.base_url = "https://www.rottentomatoes.com"
        self.search_url = f"{self.base_url}/search"
        self.session = requests.Session()
        self.session.cookies = LWPCookieJar()
        self.movie_details = {}

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

    def _extract_movie_details(self, soup, movie_og_name):
        """Extract additional movie details from the movie page and fetch poster from TMDB."""
        movie_details = {}
        
        try:
            # Extract total rating (1-10)
            # Looking at actual HTML structure where score is displayed as a percentage
            score_elems = soup.select('.scoreboard__score, [data-qa="critics-score"], .audience-score, .mop-ratings-wrap__percentage')
            for elem in score_elems:
                try:
                    text = elem.get_text().strip()
                    if '%' in text:
                        rating_value = float(text.strip('%')) / 10
                        movie_details['total_rating'] = round(rating_value, 1)
                        print(f"Found rating: {movie_details['total_rating']}")
                        break
                except ValueError:
                    continue
            
            # We'll update the poster URL later using TMDB API
            
            # Extract release year
            # Looking at the metadata section which has this information
            metadata_elems = soup.select('[slot="metadataProp"]')
            for elem in metadata_elems:
                text = elem.get_text().strip()
                # For release year
                year_match = re.search(r'Released\s+\w+\s+\d+,\s+(\d{4})', text)
                if year_match:
                    movie_details['release_year'] = int(year_match.group(1))
                    print(f"Found release year: {movie_details['release_year']}")
                    break
            
            # Extract duration
            for elem in metadata_elems:
                text = elem.get_text().strip()
                # For duration
                duration_match = re.search(r'(\d+)h\s+(\d+)m', text)
                if duration_match:
                    hours = int(duration_match.group(1))
                    mins = int(duration_match.group(2))
                    movie_details['duration'] = hours * 60 + mins
                    print(f"Found duration: {movie_details['duration']} minutes")
                    break
            
            # Extract genres
            genre_elems = soup.select('[slot="metadataGenre"]')
            if genre_elems:
                genres = []
                for elem in genre_elems:
                    genre = elem.get_text().strip()
                    if genre.endswith('/'):
                        genre = genre[:-1]  # Remove trailing slash
                    genres.append(genre)
                if genres:
                    movie_details['genres'] = genres
                    print(f"Found genres: {', '.join(movie_details['genres'])}")
            
            # Extract age rating (PG, PG-13, R, etc.)
            rating_elems = soup.select('[slot="metadataContentRating"], .meta-value:contains("Rating:"), .panel-body:contains("Rating"), div.metadata-row:contains("Rating")')
            if not rating_elems:
                # Try other selectors
                rating_elems = soup.select('.content-rating')
            if not rating_elems:
                # Try to find elements containing "Rating" text
                all_elems = soup.find_all(['div', 'span', 'p'])
                rating_elems = [elem for elem in all_elems if 'Rating' in elem.get_text() and (
                    'PG-13' in elem.get_text() or 
                    'PG' in elem.get_text() or 
                    'R ' in elem.get_text() or 
                    'G ' in elem.get_text() or 
                    'NC-17' in elem.get_text()
                )]

            if rating_elems:
                for elem in rating_elems:
                    text = elem.get_text().strip()
                    # Define patterns for common rating formats
                    rating_patterns = [
                        r'Rating:\s*(PG-13|PG|R|G|NC-17|Not Rated|NR|TV-MA|TV-14|TV-PG|TV-G|TV-Y7|TV-Y)(?:\s|$)',
                        r'Rated\s+(PG-13|PG|R|G|NC-17|Not Rated|NR|TV-MA|TV-14|TV-PG|TV-G|TV-Y7|TV-Y)(?:\s|$)',
                        r'(PG-13|PG|R|G|NC-17|Not Rated|NR|TV-MA|TV-14|TV-PG|TV-G|TV-Y7|TV-Y)(?:\s|$)',
                    ]
                    
                    for pattern in rating_patterns:
                        match = re.search(pattern, text)
                        if match:
                            rating = match.group(1)
                            movie_details['age_rating'] = rating
                            print(f"Found age rating: {rating}")
                            break
                    
                    if 'age_rating' in movie_details:
                        break
            
            # Extract synopsis
            synopsis_elem = soup.select_one('.synopsis-wrap [data-qa="synopsis-value"], [slot="description"] [slot="content"]')
            if synopsis_elem:
                movie_details['synopsis'] = synopsis_elem.get_text().strip()
                print(f"Found synopsis: {movie_details['synopsis'][:50]}...")
            
            # Extract popcornmeter (audience score) as popularity on a 1-10 scale
            popcorn_elems = soup.select('[slot="collapsedAudienceScore"], [slot="audienceScore"], .audience-score')
            for elem in popcorn_elems:
                try:
                    text = elem.get_text().strip()
                    if '%' in text:
                        percentage = int(text.strip('%'))
                        # Convert to 1-10 scale
                        movie_details['popularity'] = round(percentage / 10, 1)
                        print(f"Found popcornmeter (popularity): {percentage}% -> {movie_details['popularity']} (1-10 scale)")
                        break
                except ValueError:
                    continue
            
            # If we couldn't find the popcornmeter, try other methods
            if 'popularity' not in movie_details:
                # Look for audience score in other formats
                audience_elems = soup.select('.mop-ratings-wrap__percentage--audience, .audience-score, [data-qa="audience-score"]')
                for elem in audience_elems:
                    try:
                        text = elem.get_text().strip()
                        match = re.search(r'(\d+)%', text)
                        if match:
                            percentage = int(match.group(1))
                            # Convert to 1-10 scale
                            movie_details['popularity'] = round(percentage / 10, 1)
                            print(f"Found popcornmeter (popularity): {percentage}% -> {movie_details['popularity']} (1-10 scale)")
                            break
                    except (ValueError, AttributeError):
                        continue
            
            # Fetch poster from TMDB API
            movie_name = soup.select_one('meta[property="og:title"]')
            if movie_name:
                movie_name = movie_name.get('content', '').split(' - Rotten')[0].strip()
            else:
                movie_name = soup.select_one('h1[slot="title"]')
                if movie_name:
                    movie_name = movie_name.get_text().strip()
            
            if movie_name and 'release_year' in movie_details:
                tmdb_poster = self._get_tmdb_poster(movie_og_name, movie_details.get('release_year'))
                if tmdb_poster:
                    movie_details['poster_url'] = tmdb_poster
                    print(f"Found TMDB poster URL: {movie_details['poster_url']}")
                else:
                    # Fallback to RT poster
                    poster_elem = soup.select_one('img[slot="poster"], img.posterImage, img[data-qa="movie-poster-image"]')
                    if poster_elem and poster_elem.has_attr('src'):
                        movie_details['poster_url'] = poster_elem['src']
                        print(f"Fallback to RT poster URL: {movie_details['poster_url']}")
            
            print(f"Extracted movie details: {', '.join(movie_details.keys())}")
            
        except Exception as e:
            print(f"Error extracting movie details: {e}")
        
        return movie_details

    def _get_tmdb_poster(self, movie_name, year=None):
        """Fetch movie poster from TMDB API."""
        try:
            tmdb_api_key = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJhMTA2YzUxZDIzZTlkYjQ4OGI0MDc4YjA0ODIwMzdhZiIsIm5iZiI6MTc0MzI1OTUzOC44ODIsInN1YiI6IjY3ZTgwNzkyNmIzNjdkNDY5NTY3YmZhNCIsInNjb3BlcyI6WyJhcGlfcmVhZCJdLCJ2ZXJzaW9uIjoxfQ.xs8URWnkdu6teP6FooMttGgpyfF5qgym8wj1kjLBiKU"
            
            # Construct the URL with the movie name and optional year
            search_url = f"https://api.themoviedb.org/3/search/movie?query={quote(movie_name)}"
            if year:
                search_url += f"&year={year}"
            search_url += "&language=en-US"
            
            # Set up headers
            headers = {
                'Authorization': f'Bearer {tmdb_api_key}',
                'Content-Type': 'application/json'
            }
            
            print(f"Searching TMDB for movie: {movie_name} ({year if year else 'no year'})")
            response = requests.get(search_url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('results') and len(data['results']) > 0:
                    # If there are multiple results, get the most popular one
                    if len(data['results']) > 1:
                        # Sort by popularity (highest first)
                        sorted_results = sorted(data['results'], key=lambda x: x.get('popularity', 0), reverse=True)
                        poster_path = sorted_results[0].get('poster_path')
                    else:
                        poster_path = data['results'][0].get('poster_path')
                    
                    if poster_path:
                        poster_url = f"https://image.tmdb.org/t/p/original{poster_path}"
                        print(f"Found TMDB poster: {poster_url}")
                        return poster_url
            
            print(f"No poster found on TMDB for {movie_name}")
            return None
        
        except Exception as e:
            print(f"Error fetching TMDB poster: {e}")
            return None

    def _verify_movie_id(self, movie_id, movie_og_name):
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
                    
                    # Extract additional movie details
                    self.movie_details = self._extract_movie_details(soup, movie_og_name=movie_og_name)
                    
                    return True

                self.verified_movie_id = movie_id
                
                # Extract additional movie details
                self.movie_details = self._extract_movie_details(soup, movie_og_name=movie_og_name)
                
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

    def get_reviews(self, movie_name, movie_og_name, num_reviews=100):
        """Get reviews for a movie."""
        try:
            # Search for the movie
            movie_id = self._search_movie(movie_name)
            if not movie_id:
                print(f"Could not find movie: {movie_name}")
                return []

            # Verify movie ID
            if not self._verify_movie_id(movie_id, movie_og_name):
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
            
            # Add movie details if available
            if self.movie_details:
                for key, value in self.movie_details.items():
                    review_data[key] = value

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

def main(args):
    """Main function to run the scraper from command line."""
    scraper = RTSessionScraper()
    movie_id = scraper._search_movie(args.movie_name)

    if movie_id:
        # Create data directory if it doesn't exist
        if not os.path.exists('data'):
            os.makedirs('data')
            print("Created 'data' directory for storing JSON files")

        # Set output file path in the data directory
        output_file = args.output or f"data/{args.movie_name.lower().replace(' ', '_')}_reviews.json"
        temp_file = f"data/temp_{args.movie_name.lower().replace(' ', '_')}_reviews.json"
        
        # Get reviews
        reviews = scraper.get_reviews(movie_name=args.movie_name, num_reviews=args.num_reviews, movie_og_name=args.movie_name)
        
        # Create the base output data
        output_data = {
            "movie_name": args.movie_name,
            "movie_id": movie_id,
            "total_reviews": len(reviews),
            "reviews": reviews
        }
        
        # Add movie details if they were collected
        if hasattr(scraper, 'movie_details') and scraper.movie_details:
            print("\nAdding movie details to output data:")
            for key, value in scraper.movie_details.items():
                print(f"  - {key}: {value}")
                output_data[key] = value

        # Save to temporary file first
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        # Sanitize and save to final output file
        sanitize_reviews_json(temp_file, output_file)
        
        # Remove the temporary file as we only want to keep the sanitized version
        os.remove(temp_file)
        
        print(f"\nSanitized reviews saved to: {output_file}")
    else:
        print(f"Failed to find movie: {args.movie_name}")

if __name__ == '__main__':
    # List of 100 popular movies from different genres and eras
    movies = [
        "Titanic"
    ]

    # Process each movie
    for movie in movies:        
        args = Args(
            movie_name=movie, 
            num_reviews=20, 
            output=f"data/{movie.lower().replace(' ', '_')}_reviews.json"
        )
        main(args)
        # Add a delay between requests to avoid overwhelming the server
        time.sleep(random.uniform(2, 5))
