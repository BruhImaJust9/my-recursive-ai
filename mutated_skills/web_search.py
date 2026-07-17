from duckduckgo_search import DDGS

def execute(query: str) -> str:
    """
    Searches the live web using DuckDuckGo and returns a summary of the top results.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=4))
            
        if not results:
            return "No live search results found for this query."
            
        formatted_results = []
        for i, r in enumerate(results):
            formatted_results.append(
                f"Result {i+1}:\nTitle: {r.get('title')}\nSource: {r.get('href')}\nSnippet: {r.get('body')}\n"
            )
            
        return "\n---\n".join(formatted_results)
    except Exception as e:
        return f"Error executing web search: {str(e)}"
