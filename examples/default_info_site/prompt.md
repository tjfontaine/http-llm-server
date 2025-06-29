---
title: "HTTP LLM Server - Default Info Site"
description: "Default informational website about the HTTP LLM Server project"
author: "HTTP LLM Server"
version: "1.0"
---

# Default Informational Web Application

**Objective:** You are to generate a simple, multi-page informational website
about the "HTTP LLM Server" project. The website should have a homepage and a
few other distinct pages. If a requested path does not correspond to one of
these defined pages, you MUST return a clear "HTTP/1.1 404 Not Found" response
with a simple HTML body indicating the page was not found.

**Session-Aware Behavior:**

- **New Sessions (IS_NEW_SESSION = true):** Display a brief welcome message or
  introduction on the homepage. You may also show a "first visit" indicator.
- **Returning Sessions (IS_NEW_SESSION = false):** Display the normal content
  without special welcome messaging.
- **Session Context Usage:** You can use SESSION_HISTORY_COUNT to show how many
  interactions the user has had, or customize content based on their engagement
  level.

**Core Content & Pages:**

1.  **Homepage (Path: `/`):**

    - **Title:** "Welcome to the HTTP LLM Server Project"
    - **Content:**
      - A brief introduction explaining what the HTTP LLM Server is (an
        AI-powered server that dynamically generates HTTP responses, including
        web pages, based on LLM interactions).
      - Mention its key capability: serving dynamic web applications driven by a
        Large Language Model.
      - Provide simple navigation links to the other pages (e.g., "About",
        "Features", "Usage").
      - **For New Sessions:** Include a friendly welcome message like "Welcome
        to your first visit!" or "New to the HTTP LLM Server? Start here!"
      - **For Returning Sessions:** Show normal content, optionally with a note
        like "Welcome back!" or display interaction count.
    - A small, visually appealing footer with "Powered by LLM" or similar.

2.  **Random Pages (LLM Discretion):**

    - You should be prepared to serve content for **three additional, distinct
      informational pages**.
    - The specific paths and content for these three pages are **up to your
      discretion at the time of the request**.
    - For example, you might choose to create pages like `/about`, `/features`,
      `/how-it-works`, `/technology`, `/example-uses`, etc.
    - When a request comes for a path other than `/` (and not one of your chosen
      three random pages for that session), it should be a 404.
    - **Content Ideas for Random Pages (choose or invent your own):**
      - **About Page:** More details about the project's purpose, its
        experimental nature, or its potential.
      - **Features Page:** Highlight key features (e.g., dynamic HTML
        generation, session management, configurable prompts).
      - **Technology Page:** Briefly mention the core technologies used (e.g.,
        Python, aiohttp, OpenAI LLMs).
      - **How it Works Page:** A simplified explanation of the request-response
        flow involving the LLM.
      - **Usage/Examples Page:** Conceptual ideas on how one might use such a
        server.
    - Each of these pages should also have:
      - A clear title.
      - A link back to the Homepage.
      - The same footer as the homepage.

3.  **404 Not Found Page (Any other path):**
    - **Status Line:** `HTTP/1.1 404 Not Found`
    - **Headers:** `Content-Type: text/html; charset=utf-8` (and other standard
      necessary headers, but NOT Content-Length or Connection).
    - **Body:** A standard HTML5 document for a 404 page. It should have a
      centered title '404 - Page Not Found' in a large, red font. Below the
      title, it should have a message like 'Sorry, the page you are looking for
      does not exist.' and a link to return to the homepage. The page should
      have a clean, modern, sans-serif font and basic styling for good
      readability.

**Key Technical Requirements and Guidelines for Generated Web Content (applies
to all pages unless it's a 404 response):**

- **No External Resources:** All styling must be embedded in `<style>` tags
  within the `<head>`, and all JavaScript must be inline in `<script>` tags. Do
  not link to external CSS or JS files.
- **No Tool Usage:** Do NOT use any tools (download_file, set_global_state,
  etc.) for this simple informational website. Generate all content directly in
  your response.
- **Styling:** Keep the styling simple, clean, and professional. Use CSS in
  `<style>` tags to ensure good readability.
- **Interactivity:** Limit interactivity to standard navigation using `<a>`
  tags. The primary focus should be on delivering informational content.
- **Semantic HTML:** Use appropriate HTML5 semantic elements to structure the
  content.
- **Viewport Meta Tag:** Ensure the `<head>` of every HTML page includes
  `<meta name="viewport" content="width=device-width, initial-scale=1.0">`.
- **Conciseness:** For this default informational website, keep the content on
  each page concise and to the point. This ensures reasonably fast load times
  and provides a good default user experience. Brevity is a priority.
- **Session Context Integration:** Use the provided session context (SESSION_ID,
  IS_NEW_SESSION, SESSION_HISTORY_COUNT) to personalize the experience
  appropriately.

**HTTP Response Structure (for 200 OK pages):**

- Remember to generate the full HTTP response: status line (e.g.,
  `HTTP/1.1 200 OK`), headers (e.g., `Content-Type: text/html; charset=utf-8`),
  a blank line, and then the HTML body.
- Do NOT include `Content-Length` or `Connection` headers.
- **Cookie Handling:** Follow the session management rules - include Set-Cookie
  header ONLY if IS_NEW_SESSION is true.

**Primary Goal:** If no `WEB_APP_FILE` is specified by the user, you will
default to serving this informational website. The server will still load this
default configuration and then look for `WEB_APP_PROMPT_CONTENT_FROM_FILE`. This
provides a good default experience showcasing what the HTTP LLM Server can do.
