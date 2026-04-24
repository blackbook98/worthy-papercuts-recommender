from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from recommender import ContentBasedRecommender

app = FastAPI()
recommender = ContentBasedRecommender()


class Book(BaseModel):
    title: str
    authors: list[str] = []
    categories: list[str] = []
    description: str = ''
    rating: int = 3


class RecommendationRequest(BaseModel):
    books: list[Book]
    already_have_ids: list[str] = []  # Google Books IDs user already has
    top_n: int = 10


@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/recommend')
async def recommend(request: RecommendationRequest):
    if not request.books:
        raise HTTPException(status_code=400, detail='No books provided')

    if len(request.books) < 3:
        raise HTTPException(
            status_code=400,
            detail='At least 3 rated books needed'
        )

    books = [b.model_dump() for b in request.books]

    results = await recommender.recommend(
        finished_books=books,
        already_have_ids=request.already_have_ids,
        top_n=request.top_n,
    )

    return {'recommendations': results}