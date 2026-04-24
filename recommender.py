import numpy as np
from collections import Counter
from sklearn.feature_extraction.text import TfidfVectorizer
import httpx
import os


class ContentBasedRecommender:

    def __init__(self):
        self.google_books_api_key = os.getenv('GOOGLE_BOOKS_API_KEY')
        self.google_books_url = 'https://www.googleapis.com/books/v1/volumes'

    def extract_taste_profile(self, finished_books: list[dict]) -> dict:
        genre_scores = Counter()
        author_scores = Counter()
        keywords = []

        for book in finished_books:
            rating = book.get('rating', 3)
            weight = rating / 5.0

            for category in (book.get('categories') or []):
                clean = category.split('/')[-1].strip()
                genre_scores[clean] += weight

            for author in (book.get('authors') or []):
                author_scores[author] += weight

            description = book.get('description') or ''
            if description and rating >= 4:
                keywords.append(description)

        top_genres = [g for g, _ in genre_scores.most_common(3)]
        top_authors = [a for a, _ in author_scores.most_common(3)]

        top_keywords = []
        if keywords:
            tfidf = TfidfVectorizer(
                stop_words='english',
                max_features=10,
                ngram_range=(1, 2)
            )
            try:
                tfidf.fit_transform(keywords)
                top_keywords = list(tfidf.get_feature_names_out())[:5]
            except Exception:
                pass

        return {
            'top_genres': top_genres,
            'top_authors': top_authors,
            'top_keywords': top_keywords,
        }

    def build_search_queries(self, profile: dict) -> list[str]:
        queries = []
        genres = profile['top_genres']
        authors = profile['top_authors']
        keywords = profile['top_keywords']

        for genre in genres[:2]:
            queries.append(f'subject:{genre}')

        for author in authors[:2]:
            queries.append(f'inauthor:{author}')

        if keywords and genres:
            queries.append(f'{keywords[0]} {genres[0]}')

        if not queries:
            queries.append('bestseller fiction')

        return queries

    async def search_google_books(self, query: str) -> list[dict]:
        params = {
            'q': query,
            'maxResults': 10,
            'printType': 'books',
            'langRestrict': 'en',
        }
        if self.google_books_api_key:
            params['key'] = self.google_books_api_key

        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.google_books_url,
                params=params,
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()

        books = []
        for item in data.get('items', []):
            info = item.get('volumeInfo', {})
            books.append({
                'googleBooksId': item['id'],
                'title': info.get('title', 'Unknown'),
                'authors': info.get('authors', []),
                'categories': info.get('categories', []),
                'coverImage': info.get('imageLinks', {}).get('thumbnail'),
                'description': info.get('description', ''),
                'averageRating': info.get('averageRating'),
                'ratingsCount': info.get('ratingsCount', 0),
            })

        return books

    async def recommend(
        self,
        finished_books: list[dict],
        already_have_ids: list[str],
        top_n: int = 10
    ) -> list[dict]:

        # Step 1 — Build taste profile
        profile = self.extract_taste_profile(finished_books)
        queries = self.build_search_queries(profile)

        # Step 2 — Search Google Books for each query
        seen_ids = set(already_have_ids)
        candidates = {}

        for query in queries:
            try:
                results = await self.search_google_books(query)
                for book in results:
                    gid = book['googleBooksId']
                    if gid not in seen_ids and gid not in candidates:
                        candidates[gid] = book
            except Exception as e:
                print(f'Google Books query failed for "{query}": {e}')
                continue

        # Step 3 — Score candidates against user profile
        if not candidates:
            return []

        candidate_list = list(candidates.values())
        scores = self._score_candidates(candidate_list, profile)

        # Step 4 — Sort by score and return top N
        scored = sorted(
            zip(candidate_list, scores),
            key=lambda x: x[1],
            reverse=True
        )

        recommendations = []
        for book, score in scored[:top_n]:
            book['similarity_score'] = round(float(score), 4)
            book['reason'] = self._generate_reason(book, profile)
            recommendations.append(book)

        return recommendations

    def _score_candidates(
        self,
        candidates: list[dict],
        profile: dict
    ) -> list[float]:
        """Score each candidate book against the user's taste profile."""
        scores = []

        for book in candidates:
            score = 0.0

            # Genre match
            book_categories = {
                c.split('/')[-1].strip().lower()
                for c in (book.get('categories') or [])
            }
            for genre in profile['top_genres']:
                if genre.lower() in book_categories:
                    score += 1.5  # Genre match weighted highest

            # Author match
            book_authors = {a.lower() for a in (book.get('authors') or [])}
            for author in profile['top_authors']:
                if author.lower() in book_authors:
                    score += 0.3  # Author match weighted higher

            # Keyword match in description
            description = (book.get('description') or '').lower()
            for keyword in profile['top_keywords']:
                if keyword.lower() in description:
                    score += 1.0 # Keyword match weighted higher
            scores.append(score)

        return scores

    def _generate_reason(self, book: dict, profile: dict) -> str:
        book_categories = {
            c.split('/')[-1].strip().lower()
            for c in (book.get('categories') or [])
        }
        book_authors = {a.lower() for a in (book.get('authors') or [])}

        for author in profile['top_authors']:
            if author.lower() in book_authors:
                return f"Because you enjoy books by {author}"

        for genre in profile['top_genres']:
            if genre.lower() in book_categories:
                return f"Matches your interest in {genre}"

        if profile['top_genres']:
            return f"Popular in {profile['top_genres'][0]}"

        return "Based on your reading history"