import numpy as np
from collections import Counter
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import httpx
import os


class ContentBasedRecommender:

    def __init__(self):
        self.google_books_api_key = os.getenv('GOOGLE_BOOKS_API_KEY')
        self.google_books_url = 'https://www.googleapis.com/books/v1/volumes'

    def extract_taste_profile(self, finished_books: list[dict]) -> dict:
        genre_scores = Counter()
        author_sum = Counter()
        author_max: dict[str, float] = {}
        keywords = []

        for book in finished_books:
            rating = book.get('rating', 3)
            weight = rating / 5.0

            for category in (book.get('categories') or []):
                clean = category.split('/')[-1].strip()
                genre_scores[clean] += weight

            for author in (book.get('authors') or []):
                author_sum[author] += weight
                author_max[author] = max(author_max.get(author, 0.0), weight)

            description = book.get('description') or ''
            if description and rating >= 3:
                repeat = max(1, round(weight * 3))
                keywords.extend([description] * repeat)

        # Score = peak quality + small bonus for additional reads
        author_scores = {
            a: author_max[a] + 0.5 * (author_sum[a] - author_max[a])
            for a in author_sum
        }
        top_genres = [g for g, s in genre_scores.most_common(2) if s >= 1.0]
        top_authors = sorted(
            [a for a, s in author_scores.items() if s >= 0.8],
            key=author_scores.get,
            reverse=True
        )[:3]

        top_keywords = []
        scores = []
        if keywords:
            tfidf = TfidfVectorizer(
                stop_words='english',
                max_features=20,
                ngram_range=(1, 2)
            )
            try:
                matrix = tfidf.fit_transform(keywords)
                scores = np.asarray(matrix.sum(axis=0)).flatten()
                keywords = tfidf.get_feature_names_out()
                top_indices = scores.argsort()[::-1][:20]
                top_keywords = [keywords[i] for i in top_indices]
            except Exception:
                pass
        print('top_genres:', top_genres)
        print('top_authors:', top_authors)
        print('top_keywords:', top_keywords)
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

        if authors:
            queries.append(f'inauthor:{authors[0]}')

        if keywords:
            queries.append(' '.join(keywords[:3]))

        if keywords and genres:
            queries.append(f'{keywords[0]} {genres[0]}')
        elif genres:
            queries.append(f'subject:{genres[0]}')

        if not queries:
            queries.append('bestseller fiction')

        return queries

    async def search_google_books(self, query: str) -> list[dict]:
        print(query)
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
        seen_titles = set()
        for item in data.get('items', []):
            info = item.get('volumeInfo', {})
            title = info.get('title', '').lower().strip()
            if title in seen_titles:
                continue
            seen_titles.add(title)
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

        # Build taste profile
        profile = self.extract_taste_profile(finished_books)
        queries = self.build_search_queries(profile)
        # Search Google Books for each query
        seen_ids = set(already_have_ids)
        seen_titles = set()
        candidates = {}

        for query in queries:
            try:
                results = await self.search_google_books(query)
                added = 0
                for book in results:
                    if added >= 4:
                        break
                    gid = book['googleBooksId']
                    title = book['title'].lower().strip()
                    if gid not in seen_ids and gid not in candidates and title not in seen_titles:
                        candidates[gid] = book
                        seen_titles.add(title)
                        added += 1
            except Exception as e:
                print(f'Google Books query failed for "{query}": {e}')
                continue

        # Score candidates against user profile
        if not candidates:
            return []

        candidate_list = list(candidates.values())
        scores = self._score_candidates(candidate_list, profile)

        # Sort by score and return top N
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
        # Repeat genres/authors to upweight them in the TF-IDF space
        profile_doc = ' '.join(
            profile['top_keywords'] * 4 +
            profile['top_authors'] * 1 +
            profile['top_genres'] * 1
        )
        if not profile_doc.strip():
            return [0.0] * len(candidates)

        candidate_docs = [
            ' '.join(filter(None, [
                book.get('description') or '',
                ' '.join(book.get('categories') or []),
                ' '.join(book.get('authors') or []),
            ]))
            for book in candidates
        ]

        try:
            tfidf = TfidfVectorizer(stop_words='english', ngram_range=(1, 2))
            matrix = tfidf.fit_transform([profile_doc] + candidate_docs)
            sims = cosine_similarity(matrix[0], matrix[1:]).flatten()
            return sims.tolist()
        except Exception:
            return [0.0] * len(candidates)

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