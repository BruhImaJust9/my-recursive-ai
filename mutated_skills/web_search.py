from duckduckgo_search import DDGS

def execute(query: str) -> str:
    """
    Searches the live web using DuckDuckGo and returns a summary of the top results.
    """
    try:
        results = []
        with DDGS() as ddgs:
            # Fetch text results from DuckDuckGo
            ddgs_generator = ddgs.text(query, max_results=4)
            if ddgs_generator:
                results = list(ddgs_generator)
            
        if not results:
            return "No live search results found for this query."
            
        formatted_results = []
        for i, r in enumerate(results):
            # DuckDuckGo uses 'body' for the text snippet
            snippet = r.get('body', r.get('snippet', ''))
            formatted_results.append(
                f"Result {i+1}:\nTitle: {r.get('title')}\nSource: {r.get('href')}\nSnippet: {snippet}\n"
            )
            
        return "\n---\n".join(formatted_results)
    except Exception as e:
        return f"Error executing web search: {str(e)}"
