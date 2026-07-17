def execute(user_input):
    # Safely calculate basic math expressions string input
    try:
        # Clean up text inputs to allow pure math processing
        clean_expr = user_input.replace("x", "*").replace(" ", "")
        return eval(clean_expr, {"__builtins__": None}, {})
    except Exception as e:
        return f"Error computing expression: {e}"
