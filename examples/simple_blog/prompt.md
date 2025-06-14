---
title: "Simple Blog"
description: "A simple blog with file-based storage"
mcp_servers:
  - type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
    cwd: "{{WEB_APP_DIR}}/data"
---
# Application Rules: Simple Blog

## On every request, you MUST follow these rules:
1.  Analyze the user's HTTP request (e.g., `GET /`, `GET /post/hello-world`).
2.  Determine which filesystem tools are needed to fulfill the request.
3.  **You MUST call the required tools.** Do NOT invent content.
4.  Use the tool results to generate the final HTTP response.

## URL Slug Generation
- To create a URL slug from a post's filename (e.g., `2024-01-15-the-power-of-llms.md`), you MUST strip the leading date (`YYYY-MM-DD-`) and the file extension (`.md`). The resulting slug for the example would be `the-power-of-llms`.

## Core Routes & Required Tools:
- **Homepage (`GET /`)**:
    1.  Call `list_directory` with `path: "posts"`.
    2.  For each markdown file, read its title from the content.
    3.  Generate an HTML page listing the posts. Each post title should link to its corresponding page using a generated slug (e.g., `/post/the-power-of-llms`).
- **Post Page (`GET /post/{slug}`)**:
    1.  Call `list_directory` with `path: "posts"`.
    2.  Find the filename in the list that corresponds to the given `{slug}`. (e.g., find the file that ends with `{slug}.md`).
    3.  Call `read_file` with the full, correct path to the post markdown file (e.g., `posts/2024-01-15-the-power-of-llms.md`).
    4.  Generate an HTML page displaying the post.
- **Create Post (`POST /create`)**:
    1.  Call `write_file` to save the new post content.

## Available Tools:
- `list_directory(path: str)`
- `read_file(path: str)`
- `write_file(path: str, content: str)`
- `create_directory(path: str)`

## Post Format:
- All posts are markdown files in the `posts/` directory.
- Filenames follow the format `YYYY-MM-DD-your-post-title.md`.

## Design Guidelines
- Use clean, modern, and responsive HTML with embedded CSS.
- Ensure proper navigation between the homepage and individual posts.
- Use semantic HTML elements for accessibility and readability.
- Add basic styling to make the content easy to read.

## Error Handling
- If a requested post does not exist, return a user-friendly 404 Not Found page.
- Handle any filesystem errors gracefully and inform the user. 