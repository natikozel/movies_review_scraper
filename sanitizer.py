import json

def sanitize_reviews_json(input_file, output_file=None):
    # If no output file specified, we'll overwrite the input file
    if output_file is None:
        output_file = input_file
    
    # Read the JSON file
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Remove 'author' from each review
    for review in data['reviews']:
        if 'author' in review:
            del review['author']
    
    # Write the sanitized data back to file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# Example usage
sanitize_reviews_json('interstellar_reviews.json')