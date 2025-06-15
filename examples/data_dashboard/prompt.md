---
title: "Data Analytics Dashboard (SPA)"
description:
  "A Single-Page Application dashboard for data analytics, built with Alpine.js."
mcp_servers:
  - type: stdio
    command: uvx
    args: ["database-mcp"]
    env:
      DB_TYPE: sqlite
      DB_CONFIG: '{"dbpath": "{{WEB_APP_DIR}}/data/analytics.db"}'
  - type: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
    cwd: "{{WEB_APP_DIR}}"
author: "AI Assistant"
version: "2.0"
---

# Data Analytics Dashboard (SPA)

You are to build a Single-Page Application (SPA) for data analytics. The
frontend should be a single `index.html` file that uses **Alpine.js** for all
dynamic behavior and component-based rendering.

## Application Architecture

The application is a single `index.html` file that acts as the main application
"shell". All "pages" are HTML partials that are loaded dynamically from the
backend and injected into the main content area. This avoids full page reloads
and minimizes the initial payload.

### Dynamic Page Loading

- When the user navigates to a page for the first time (e.g., by clicking a nav
  link), the application will fetch an HTML partial from a backend endpoint like
  `GET /api/pages/{page_name}`.
- The returned HTML will be injected into the main content area of the shell.
  The `x-html` attribute in Alpine.js is a good candidate for this.
- Page content should be cached on the client-side after the first load to make
  subsequent navigation instantaneous.
- Page-specific JavaScript logic must be contained within that page's root
  Alpine.js component, as described below.

### HTML Partial Structure

When creating HTML partials to be loaded dynamically, it is critical to
structure them correctly for Alpine.js. The `x-html` directive used in the main
shell **does not execute `<script>` tags** from the loaded content for security
reasons.

Therefore, any Alpine.js component logic required for a partial must be
self-contained within that partial's HTML. This is achieved by placing all
JavaScript logic for a component—including its data, state, and functions (like
`init` or data fetching methods)—directly inside the `x-data` attribute of the
component's root HTML element.

Do not rely on separate `<script>` tags within the partial to define functions
or component data, as they will not be executed when the content is injected
into the page. All behavior for a dynamically loaded view must be encapsulated
within its `x-data` attribute.

### Data Reloading

Every page or component that displays data fetched from an API must provide a
"Reload" or "Refresh" button, allowing users to re-fetch the data on demand.

- The logic to fetch data for a component must be encapsulated in a reusable
  function within its Alpine.js definition.
- This function should be called automatically when the component is first
  loaded.
- The "Reload" button in the UI must be wired to call this same function.
- For charts, it is important to first destroy the existing chart instance
  before creating a new one with the refreshed data. This prevents rendering
  errors and memory leaks.

### Initial `index.html` Structure

The `index.html` shell should contain:

- A main Alpine.js data object to manage application state, including the
  current view.
- A navigation bar.
- A main content area where different views are rendered.
- Initial views for:
  - **Dashboard**: Overview of key metrics.
  - **Analytics**: Interactive charts.
  - **Reports**: File list from the `reports` directory.
  - **Market Research**: Web search interface.

### Backend API

You will define and implement backend API endpoints that the Alpine.js
components will call to fetch data. For example:

- `GET /api/kpis`: Fetch key performance indicators for the main dashboard.
- `GET /api/sales-data?timespan=7d`: Fetch sales data for charts.
- `GET /api/reports`: List available reports.
- `GET /api/pages/{page_name}`: Fetch the HTML content for a specific page
  (e.g., `/api/pages/analytics`).

**Crucially, you should be prepared to add new API endpoints and corresponding
frontend components when the user wants to "dive deeper" into the data.** For
example, if a user clicks on a specific product category on a chart, you might:

1.  Define a new API endpoint like `GET /api/products/electronics`.
2.  Create a new view in `index.html` to display detailed data for that
    category.
3.  Update the UI to navigate to this new view.

### Application and Session Initialization

To ensure the application is always in a valid state, you must perform an
initialization check on every request. This process distinguishes between the
one-time application data priming and the per-server-run caching of the database
schema.

**On every request, you MUST follow these steps:**

1.  **Check for Cached Schema**: First, check the `GLOBAL_STATE` to see if a key
    named `db_info` exists. This key acts as an in-memory cache for the database
    schema for the current server instance.

2.  **If `db_info` is NOT in `GLOBAL_STATE` (Schema Discovery & Priming):** This
    block is executed once per server run to discover the database state and
    prime it if necessary. a. **Discover Database**: Use the `database-mcp`
    server to call `list_databases` to get the `db_id`, then call
    `get_database_info` to retrieve the schema. b. **Check if Priming is
    Needed**: Examine the result from `get_database_info`. If it's empty or
    indicates no tables exist, the persistent application storage is
    uninitialized and you **must** perform the one-time priming process. c.
    **Priming Process (if needed):** i. Use the `filesystem` server's
    `create_directory` tool to create the `data/` and `data/reports/`
    directories. ii. Use the `database-mcp` server's `execute_query` tool to run
    all necessary `CREATE TABLE` statements for `sales_data`, `user_metrics`,
    `marketing_campaigns`, and `financial_metrics`. iii. Use the `database-mcp`
    server's `execute_query` tool to populate the tables. **To avoid exceeding
    turn limits, you MUST insert data efficiently.** For each table, generate a
    single `INSERT` statement that includes multiple sample records (e.g., using
    multiple `VALUES (...)` clauses) to minimize tool calls. A few
    representative rows per table is sufficient. iv. Use the `filesystem`
    server's `write_file` tool to create a variety of sample reports inside
    `data/reports/`. v. After priming is complete, call `get_database_info`
    again to get the newly created schema. d. **Cache the Schema**: Call
    `set_global_state` to store the database schema JSON (retrieved in step 2a
    or 2c-v) in the `db_info` key. This populates the cache for all future
    requests on this server instance.

3.  **If `db_info` IS in `GLOBAL_STATE`:**
    - The schema is already cached. You must use this information for all
      database-related operations during the request.

This entire process ensures that the application self-initializes on its
first-ever run and that all subsequent server instances correctly load the
database schema into a cache for efficient operation.

### Chart Data API Contract

Endpoints that provide data for charts must return a JSON object that is
directly consumable by the Chart.js constructor. This standardizes the interface
between the frontend and backend. The root of the JSON object should contain
properties for `type` (e.g., "bar"), `data`, and `options`. The `data` property
should be an object containing `labels` and `datasets` arrays. The `options`
property should be an object containing the chart configuration, such as
responsiveness, plugins, and scales. This structure ensures that the entire JSON
response can be passed directly to `new Chart(canvas, responseJson)` in the
frontend.

### Database Schema and Queries

You will be interacting with a SQLite database that has been pre-populated with
sample data. The database MCP server is configured with a single, default
database connection.

The database contains the following tables. You must use these exact table and
column names when constructing SQL queries.

- **`sales_data`**: Records individual sales transactions.

  - `date`: The date of the sale.
  - `product_category`: The category of the product sold (e.g., "Electronics",
    "Software").
  - `product_name`: The specific name of the product.
  - `revenue`: The total revenue from the sale.
  - `units_sold`: The number of units sold.
  - `region`: The geographical region of the sale.
  - `sales_rep`: The name of the sales representative.

- **`user_metrics`**: Contains daily user engagement metrics.

  - `date`: The date for which the metrics are recorded.
  - `active_users`: The number of active users on that day.
  - `new_users`: The number of new user sign-ups.
  - `page_views`: The total number of page views.
  - `session_duration`: The average session duration in minutes.
  - `bounce_rate`: The percentage of single-page sessions.

- **`marketing_campaigns`**: Details on marketing campaigns.

  - `campaign_name`: The name of the campaign.
  - `start_date`: The start date of the campaign.
  - `end_date`: The end date of the campaign.
  - `budget`: The allocated budget for the campaign.
  - `spend`: The actual amount spent.
  - `impressions`: The number of times the campaign was displayed.
  - `clicks`: The number of clicks the campaign received.
  - `conversions`: The number of conversions resulting from the campaign.
  - `channel`: The marketing channel used (e.g., "Social Media", "Google Ads").

- **`financial_metrics`**: Monthly financial data broken down by department.
  - `date`: The date representing the start of the month for the recorded
    metrics.
  - `revenue`: The revenue for the period.
  - `expenses`: The expenses for the period.
  - `profit`: The calculated profit.
  - `cash_flow`: The net cash flow.
  - `department`: The business department (e.g., "Sales", "Engineering").

## Core Features

### Database Analytics

- The backend should query the SQLite database.
- The frontend will visualize this data using Chart.js.

### Data Visualization with Chart.js

- All data visualizations must be rendered using Chart.js.
- A chart partial will consist of an Alpine.js component defined on a root
  `<div>` element, which contains a `<canvas>` element for the chart itself.
- The Alpine component's logic, defined in the `x-data` attribute, is
  responsible for fetching the chart data from a backend endpoint.
- Upon receiving the data, the component must use `this.$nextTick()` to ensure
  the `<canvas>` element is rendered in the DOM before initializing the chart.
- The `new Chart(canvasElement, chartConfig)` instance should be stored within
  the Alpine component's state (e.g., `this.chart = new Chart(...)`). This
  allows the chart to be referenced later for updates or destruction.

### Report Management

- The `/api/reports` endpoint will list files in the `data/reports` directory.
- The frontend will display this list and allow users to view them.

### Market Intelligence

- Use the mcpoogle server for web searches.
- Create a view for making searches and displaying results.

## Technical Requirements

### Frontend (Alpine.js)

- The main `index.html` file must include all required JavaScript and CSS
  libraries in its `<head>` tag to ensure they are available globally before any
  dynamic content is rendered.
- The required libraries, which should be loaded from their official CDNs, are:
  - The latest v3 release of Alpine.js, loaded deferred.
  - The latest v4 release of Chart.js.
  - The latest release of Tailwind CSS.
- Use Alpine.js for all DOM manipulation and state management.
- Implement a client-side routing and data-fetching mechanism. A top-level
  Alpine.js component should manage the current page, fetch new page content
  from the backend, cache it, and display it.
- Use `x-data` to define components and their state.
- Use `x-show` or `x-if` to manage which view is currently visible.
