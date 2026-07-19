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
# ... inside your search function where the response is processed ...
if response.status_code == 200:
    data = response.json()
    
    # Safely look for common search engine labels
    if "organic" in data:
        results = [item.get("snippet", "") for item in data["organic"]]
    elif "results" in data:
        results = [item.get("snippet", "") for item in data["results"]]
    else:
        results = ["Layout shift detected in search response data."]
        
    return "\n\n".join(results[:4])
else:
    return f"Connection error: {response.status_code}"
