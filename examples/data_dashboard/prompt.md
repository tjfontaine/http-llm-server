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
version: "2.11"
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

## Routing and Initialization (Server-Side)

The application follows a specific initialization workflow to ensure the
database is ready before serving content.

### The `/_prewarm` Endpoint

This endpoint is responsible for all one-time application setup.

**When a request is received for `GET /_prewarm`:**

1.  **Check Initialization State**: Use the `get_global_state` tool to check if
    the `db_initialized` key is `"true"`. If it is, you MUST immediately respond
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

## Application Architecture (Client-Side)

The application is a true Single-Page Application (SPA). The entire frontend is
contained within a single `index.html` file. All views are defined as HTML
templates within this file and are conditionally rendered based on the
application's state. This eliminates the need for full page reloads and avoids
fetching HTML content from the server after the initial load.

### Client-Side State Management: The Global Store

A central, global data store will be the single source of truth for all shared
application state. This store will be implemented as an Alpine.js `x-data` object
attached to the `<body>` or a top-level `<div>`. It is responsible for:

-   Tracking the currently active view (e.g., `dashboard`, `analytics`, `data-table`).
-   Caching data retrieved from the backend API to prevent redundant network requests.
-   Maintaining a global loading status (`isLoading: boolean`) to provide clear visual feedback to the user when data is being fetched in the background.
-   Providing a centralized asynchronous function (`fetchData(queryObject)`) for all communication with the backend API (`POST /api/query`). This function should handle `fetch` calls, JSON parsing, and error handling, updating `isLoading` status appropriately.
-   Initializing the application's client-side routing by listening for changes to the URL hash (`window.location.hash`).

**Crucial Alpine.js `x-data` Structure for Global Store:**

When defining the `x-data` for the global store, ensure it is structured as a plain JavaScript object. This object will contain both reactive properties and methods. The `fetchData` method MUST be a direct property of this object, defined as an asynchronous function.

For example, the `x-data` object should include properties like `activeView`, `isLoading`, `cachedData`, and `errorMessage`. It should also include methods like `init()` for initial setup (e.g., reading the URL hash and setting up `onhashchange` listener) and `fetchData(queryObject)`. The `fetchData` method should use the `fetch` API to make a POST request to `/api/query`, setting the `Content-Type` header to `application/json` and sending the `queryObject` as a JSON string in the request body. It must handle successful responses by parsing the JSON and returning the data, and handle errors by setting `errorMessage` and logging to the console. It must also manage the `isLoading` property, setting it to `true` before the fetch call and `false` in a `finally` block.

When accessing `fetchData` from within other Alpine.js components or elements, you will use `this.fetchData()` if the global store is the primary `x-data` on the `<body>` or a parent element. If you define the global store using `Alpine.store('globalStore', {...})`, then you would access it via `$store.globalStore.fetchData()`.

### Client-Side Routing

Routing is managed by tracking the active view in the global store. Navigation
links in the application will update the URL hash (e.g., to `#/dashboard` or
`#/analytics`). An event listener on `window.onhashchange` will detect changes
to the hash and automatically update the `activeView` property in the global
store, which in turn will cause the corresponding view to be displayed using
Alpine.js's conditional rendering (`x-show`). The initial view should be determined
from `window.location.hash` on `x-init`.

### Initial `index.html` Structure

The main HTML file will be structured to support the SPA architecture. Its
`<head>` section will include the necessary CSS and JavaScript libraries
(Pico.css, Chart.js, Alpine.js) via CDN links. The `<body>` will contain a main
header with navigation links, a global loading indicator (visible via `x-show`
when `isLoading` is true), and a main content area where the different
application views are dynamically rendered from templates using `x-show` based
on the `activeView`.

## Client-Side Technologies: Modern Usage

### Alpine.js (v3.x)

Alpine.js is used for all dynamic behavior. Focus on its core directives:

-   `x-data`: Defines a new Alpine component scope and its reactive data. Use it for both the global store and individual view components.
-   `x-init`: Executes JavaScript once when the component is initialized. Ideal for initial data fetching or chart setup.
-   `x-bind` (`:` shorthand): Dynamically binds HTML attributes to data properties (e.g., `:class="{ 'hidden': isLoading }"`).
-   `x-on` (`@` shorthand): Attaches event listeners (e.g., `@click="doSomething()"`).
-   `x-text`: Updates the text content of an element.
-   `x-html`: Updates the inner HTML of an element (use with caution, sanitize input).
-   `x-show`: Conditionally displays or hides an element based on a boolean expression.
-   `x-for`: Renders a list of elements based on an array.
-   `$watch`: Observe changes to a data property and react to them (e.g., re-render a chart when its data changes).
-   `$nextTick`: Ensures a function runs after Alpine has made its DOM updates.

### Chart.js (v4.x)

Chart.js is used for data visualization. Charts should be created and managed
within Alpine.js components. When data for a chart changes, the existing Chart.js
instance should be updated using its `update()` method, rather than creating a new
chart. Ensure the canvas element is available in the DOM before initializing a chart.

-   **Initialization**: Create a new `Chart` instance, passing the canvas context and configuration object.
-   **Data Structure**: Charts expect data in a specific JSON format (e.g., `labels: [], datasets: [{ label: '', data: [] }]`). Ensure your backend API returns data in this format or transform it on the frontend.
-   **Updating**: Modify the `data` property of an existing chart instance and call `chart.update()`.
-   **Destruction**: If a chart component is removed from the DOM, its Chart.js instance should be destroyed to prevent memory leaks.

### Modern JavaScript (`fetch` API, `async/await`)

All backend communication MUST use the `fetch` API with `async/await` syntax.

-   **`fetch(url, options)`**: Make HTTP requests. For `POST /api/query`, ensure `method: 'POST'`, `headers: { 'Content-Type': 'application/json' }`, and `body: JSON.stringify(queryObject)`.
-   **`response.json()`**: Parse JSON responses.
-   **`response.ok`**: Check for successful HTTP status codes (2xx).
-   **`try...catch`**: Handle network errors or API errors gracefully. Update the global `isLoading` state and display user-friendly messages.

## Detailed View Specifications (Client-Side Implementation)

Each view is a self-contained component that is displayed based on the current
route. Each should be responsible for requesting its own data via the global
store's `fetchData` function and managing its own loading states if specific to the view.

### Dashboard View (`#/dashboard`)

This view serves as the application's main landing page, presenting a high-level
overview of the dataset. It should display several "Key Performance Indicator"
(KPI) cards showing metrics like the total number of power plants, the combined
total capacity, the number of countries represented (using the `country_long`
column), and the most common primary fuel type. Below the KPIs, this view should
feature a bar chart visualizing the top 5 countries by total power capacity. The
view should fetch this data upon its initial load using `x-init`.

### Analytics View (`#/analytics`)

The analytics view provides more detailed, interactive charts for deeper data
exploration. It should feature at least three distinct charts:

1.  A doughnut chart showing the distribution of total capacity by primary fuel
    type.
2.  A horizontal bar chart displaying the top 10 countries by the number of
    power plants.
3.  A line chart illustrating the trend of total capacity additions based on the
    commissioning year of the plants.

Each chart in this view should have its own dedicated "Refresh" button, allowing
the user to reload its data on demand. Use `$watch` on relevant data properties
to automatically update charts when their underlying data changes.

### Data Table View (`#/data-table`)

This view presents the raw data in a searchable, filterable, and paginated
table. It must provide users with the ability to perform a case-insensitive text
search on plant names and countries (using the `country_long` column), as well
as filter the data by country and primary fuel type using dropdown menus. The
table should display key details for each plant and include pagination controls
(Previous/Next buttons, page number display) to navigate through large result
sets. This view must also include an "Export as CSV" button that allows users to
download the currently filtered set of data.

### Market Research View (`#/market-research`)

This view integrates a web search feature to provide external context. It should
have a search input field and button. When a user performs a search, the results
should be displayed clearly. Each search result must have a "Save Insight"
button that, when clicked, persists that result's information to the database. A
separate section on this page, titled "Saved Insights," should list all insights
that have been previously saved, loading them from the database when the view is
first displayed.

### Database Schema View (`#/schema`)

This view acts as a simple database schema explorer. It should first fetch and
display a list of all tables in the database. When a user selects a table from
the list, the view should then fetch and display that table's schema, including
column names, data types, and other relevant details, in a clear, tabular
format.

## Backend API Specification (Server-Side)

To provide a flexible and efficient data-fetching mechanism, the backend will
expose a single, GraphQL-like API endpoint. This approach allows the frontend to
request all the data it needs for a given view in a single, targeted request.

-   **Endpoint**: `POST /api/query`

Instead of hitting multiple REST endpoints, the frontend will send a request to
this single endpoint with a JSON body describing the data it needs. For example:

-   **For KPIs**: The query would ask for a `kpis` object and specify the required
    fields, like `plant_count` or `total_capacity_mw`.
-   **For the Data Table**: The query would ask for a `plants` list and could
    include arguments for pagination, searching, and filtering, as well as specify
    which data fields for each plant should be returned.
-   **For Charts**: The query would ask for a `chart` by name. The backend would
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

-   **CSS Framework**: Use a lightweight, class-less CSS framework like
    **Pico.css** by including its CDN link in the `<head>` of `index.html`. Use
    the latest stable version from a reliable CDN (e.g., `https://cdn.jsdelivr.net/npm/@picocss/pico@1.*/css/pico.min.css`).
-   **Responsiveness**: The layout should be responsive and usable on both desktop
    and mobile devices. Utilize Pico.css's responsive features.
-   **Feedback**: Provide loading indicators (e.g., spinners) while data is being
    fetched from the API. Display user-friendly messages for errors or when no
    data is available. The global `isLoading` state should drive these indicators.

## Error Handling

-   **Frontend**: API call failures (e.g., network error, server error) should be
    caught gracefully within the `fetchData` function. Display a user-friendly
    error message to the user (e.g., "Failed to load data. Please try refreshing.").
    Do not show raw error messages from the console.
-   **Backend**: The API should return appropriate HTTP status codes (e.g.,
    `404 Not Found`, `400 Bad Request` for invalid parameters,
    `500 Internal Server Error`). Error responses should be in a consistent JSON
    format, like `{"error": "A description of the error."}`.

## CDN Resources (Mid-2024)

Ensure you use the following CDN links for the specified libraries to target modern browsers (Google Chrome, Apple Safari) from mid-2024:

-   **Pico.css (v1.5.10 or later)**:
    `https://cdn.jsdelivr.net/npm/@picocss/pico@1.5.10/css/pico.min.css`
-   **Alpine.js (v3.13.10 or later)**:
    `https://cdn.jsdelivr.net/npm/alpinejs@3.13.10/dist/cdn.min.js`
-   **Chart.js (v4.4.3 or later)**:
    `https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js`

Include these in the `<head>` section of your `index.html`.
