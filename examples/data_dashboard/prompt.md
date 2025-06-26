---
title: "Global Power Plant Dashboard (SPA)"
description:
  "A Single-Page Application dashboard for analyzing global power plant data,
  built with Alpine.js."
mcp_servers:
  - type: stdio
    command: npx
    args:
      - "-y"
      - "@executeautomation/database-server"
      - "{{WEB_APP_DIR}}/data/global-power-plants.db"
  - type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
    cwd: "{{WEB_APP_DIR}}"
author: "AI Assistant"
version: "2.8"
---

# Global Power Plant Dashboard (SPA)

🚨 **CRITICAL RULE: DO NOT READ THE DATABASE FILE DIRECTLY** 🚨

The database file at `{{WEB_APP_DIR}}/data/global-power-plants.db` is a binary
SQLite file. You **MUST NOT** under any circumstances use the `read_file` tool
to access it. Doing so will result in a critical error.

- To check if the file exists, use the `filesystem` MCP server's `get_file_info`
  tool.
- To interact with the database contents, use the `database` MCP server's
  `read_query` tool.

---

## Routing and Initialization

The application follows a specific initialization workflow to ensure the
database is ready before serving content.

### The `/_prewarm` Endpoint

This endpoint is responsible for all one-time application setup.

**When a request is received for `GET /_prewarm`:**

1.  **Check Initialization State**: Use the `get_global_state` tool to check if
    the `db_initialized` key is `true`. If it is, you MUST immediately respond
    with a `302 Found` redirect to `/`. For example:
    ```http
    HTTP/1.1 302 Found
    Location: /

    ```
2.  **Ensure Database Exists**: Use the `filesystem` MCP server's
    `get_file_info` tool to determine if
    `{{WEB_APP_DIR}}/data/global-power-plants.db` exists. **DO NOT use
    `read_file`**.
3.  **Validate Database Integrity**: If the file exists, use the `database` MCP
    server to verify:
    - The database can be opened without errors.
    - It contains the `global-power-plants` table.
    - The table has data (is not empty).
4.  **Download if Needed**: If the file does not exist, is corrupted, or
    validation fails:
    - Use the `download_file` tool to fetch it from
      `https://datasette.io/global-power-plants.db` and save it to
      `{{WEB_APP_DIR}}/data/global-power-plants.db`. This tool will create the
      directory if needed.
    - Re-validate the downloaded database to ensure it's correct.
5.  **Set State and Redirect**: Once the database is successfully validated, use
    the `set_global_state` tool to set the key `db_initialized` to the string
    `"true"`. After setting the state, you MUST respond with a `302 Found`
    redirect, setting the `Location` header to `/`. For example:
    ```http
    HTTP/1.1 302 Found
    Location: /

    ```
6.  **Handle Failure**: If the database cannot be validated even after
    downloading, you MUST respond with a `500 Internal Server Error` and a plain
    text message indicating the failure. For example:
    ```http
    HTTP/1.1 500 Internal Server Error
    Content-Type: text/plain

    Failed to initialize the database.
    ```

### All Other Routes (e.g., `/`, `/dashboard`, `/api/query`)

**Before processing any other request:**

1.  **Check Initialization State**: Use the `get_global_state` tool to check if
    the `db_initialized` key is set to `"true"`.
2.  **Serve Landing Page if Not Initialized**: If `db_initialized` is not
    `"true"`, you MUST respond with a `200 OK` status and the following HTML
    content. **DO NOT PROCEED WITH ANY OTHER ACTIONS.**
    ```html
    <!DOCTYPE html>
    <html lang="en">
      <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>Application Not Ready</title>
        <style>
          body {
            font-family: sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background-color: #f0f0f0;
          }
          .container {
            text-align: center;
            padding: 2rem;
            border: 1px solid #ccc;
            border-radius: 8px;
            background-color: #fff;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
          }
          a {
            color: #007bff;
            text-decoration: none;
          }
          a:hover {
            text-decoration: underline;
          }
        </style>
      </head>
      <body>
        <div class="container">
          <h1>Application Initializing</h1>
          <p>
            The application is not yet ready. Please initialize it by visiting
            the prewarm URL.
          </p>
          <p><a href="/_prewarm">Click here to initialize</a></p>
        </div>
      </body>
    </html>
    ```
3.  **Proceed if Initialized**: If `db_initialized` is `"true"`, proceed with
    handling the request as described in the sections below (Application
    Architecture, Detailed View Specifications, etc.).

---

You are to build a Single-Page Application (SPA) for data analytics, focusing on
a dataset of global power plants. The frontend should be a single `index.html`
file that uses **Alpine.js** for all dynamic behavior and component-based
rendering. The application should have a clean, modern, and responsive design.

The data for this application comes from the
[Global Power Plant Database](https://datasets.wri.org/dataset/global-power-plant-database),
made available via a
[Datasette project](https://global-power-plants.datasettes.com/global-power-plants/global-power-plants).

## Application Architecture

The application is a true Single-Page Application (SPA). The entire frontend is
contained within a single `index.html` file. All views are defined as HTML
templates within this file and are conditionally rendered based on the
application's state. This eliminates the need for full page reloads and avoids
fetching HTML content from the server after the initial load.

### Client-Side State Management (The Global Store)

A central, global data store will be the single source of truth for all shared
application state. This store is responsible for:

- Tracking the currently active view (e.g., dashboard, analytics).
- Caching data retrieved from the backend API to prevent redundant network
  requests.
- Maintaining a global loading status to provide clear visual feedback to the
  user when data is being fetched in the background.
- Providing a centralized function for all communication with the backend API.
- Initializing the application's client-side routing by listening for changes to
  the URL hash.

### Client-Side Routing

Routing is managed by tracking the active view in the global store. Navigation
links in the application will update the URL hash (e.g., to `#/dashboard` or
`#/analytics`). A listener will detect changes to the hash and automatically
update the global store, which in turn will cause the corresponding view to be
displayed.

### Initial `index.html` Structure

The main HTML file will be structured to support the SPA architecture. Its
`<head>` section will include the necessary CSS and JavaScript libraries
(Pico.css, Chart.js) and the script that defines the global application store.
The `<body>` will contain a main header with navigation links, a global loading
indicator that is shown during API calls, and a main content area where the
different application views are dynamically rendered from templates.

---

## Detailed View Specifications

Each view is a self-contained component that is displayed based on the current
route. Each should be responsible for requesting its own data via the global
store's fetch function.

### Dashboard View (`/dashboard`)

This view serves as the application's main landing page, presenting a high-level
overview of the dataset. It should display several "Key Performance Indicator"
(KPI) cards showing metrics like the total number of power plants, the combined
total capacity, the number of countries represented (using the `country_long`
column), and the most common primary fuel type. Below the KPIs, this view should
feature a bar chart visualizing the top 5 countries by total power capacity. The
view should fetch this data upon its initial load.

### Analytics View (`/analytics`)

The analytics view provides more detailed, interactive charts for deeper data
exploration. It should feature at least three distinct charts:

1.  A doughnut chart showing the distribution of total capacity by primary fuel
    type.
2.  A horizontal bar chart displaying the top 10 countries by the number of
    power plants.
3.  A line chart illustrating the trend of total capacity additions based on the
    commissioning year of the plants.

Each chart in this view should have its own dedicated "Refresh" button, allowing
the user to reload its data on demand.

### Data Table View (`/data-table`)

This view presents the raw data in a searchable, filterable, and paginated
table. It must provide users with the ability to perform a case-insensitive text
search on plant names and countries (using the `country_long` column), as well
as filter the data by country and primary fuel type using dropdown menus. The
table should display key details for each plant and include pagination controls
(Previous/Next buttons, page number display) to navigate through large result
sets. This view must also include an "Export as CSV" button that allows users to
download the currently filtered set of data.

### Market Research View (`/market-research`)

This view integrates a web search feature to provide external context. It should
have a search input field and button. When a user performs a search, the results
should be displayed clearly. Each search result must have a "Save Insight"
button that, when clicked, persists that result's information to the database. A
separate section on this page, titled "Saved Insights," should list all insights
that have been previously saved, loading them from the database when the view is
first displayed.

### Database Schema View (`/schema`)

This view acts as a simple database schema explorer. It should first fetch and
display a list of all tables in the database. When a user selects a table from
the list, the view should then fetch and display that table's schema, including
column names, data types, and other relevant details, in a clear, tabular
format.

## Backend API Specification (GraphQL-like)

To provide a flexible and efficient data-fetching mechanism, the backend will
expose a single, GraphQL-like API endpoint. This approach allows the frontend to
request all the data it needs for a given view in a single, targeted request.

- **Endpoint**: `POST /api/query`

Instead of hitting multiple REST endpoints, the frontend will send a request to
this single endpoint with a JSON body describing the data it needs. For example:

- **For KPIs**: The query would ask for a `kpis` object and specify the required
  fields, like `plant_count` or `total_capacity_mw`.
- **For the Data Table**: The query would ask for a `plants` list and could
  include arguments for pagination, searching, and filtering, as well as specify
  which data fields for each plant should be returned.
- **For Charts**: The query would ask for a `chart` by name. The backend would
  then be responsible for executing the correct underlying database query and
  returning a pre-structured JSON object that is directly consumable by the
  charting library on the frontend.

The backend will parse these queries, use the database server's `read_query`
tool to construct and execute the appropriate SQL statements, and then return a
JSON response that mirrors the structure of the original query.

**IMPORTANT**: The table in the database is named `global-power-plants`. All SQL
queries MUST use this exact table name, enclosed in double quotes (e.g.,
`SELECT * FROM "global-power-plants";`) because of the hyphen in the name. The
column for country names is `country_long`.

**DATABASE FILE LOCATION**: All file operations must use the `{{WEB_APP_DIR}}`
placeholder which will be replaced with the actual web application directory
path. The database file should be located at
`{{WEB_APP_DIR}}/data/global-power-plants.db`. When using the `download_file`
tool, ensure you download to this exact path.

## Styling and UX

- **CSS Framework**: Use a lightweight, class-less CSS framework like
  **Pico.css** by including its CDN link in the `<head>` of `index.html`. This
  ensures a clean, modern aesthetic with minimal effort.
- **Responsiveness**: The layout should be responsive and usable on both desktop
  and mobile devices.
- **Feedback**: Provide loading indicators (e.g., spinners) while data is being
  fetched from the API. Display user-friendly messages for errors or when no
  data is available.

## Error Handling

- **Frontend**: API call failures (e.g., network error, server error) should be
  caught gracefully. Display a user-friendly error message to the user (e.g.,
  "Failed to load data. Please try refreshing."). Do not show raw error messages
  from the console.
- **Backend**: The API should return appropriate HTTP status codes (e.g.,
  `404 Not Found`, `400 Bad Request` for invalid parameters,
  `500 Internal Server Error`). Error responses should be in a consistent JSON
  format, like `{"error": "A description of the error."}`.
