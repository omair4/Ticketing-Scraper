-- Yapsody scraper schema
-- Mirrors prekindle_kevins_structure + Yapsody-specific cols

CREATE TABLE event_discovery (
    event_id        VARCHAR(20) PRIMARY KEY,
    venue_code      VARCHAR(100),
    event_name      VARCHAR(255),
    venue_name      VARCHAR(255),
    event_datetime  DATETIME,
    booking_url     TEXT,                  -- {venue_code}.yapsody.com/event/index/{id}/{slug}
    ticket_url      TEXT,                  -- booking page URL (book/{event_id}/{perf_id})
    scraper_type    VARCHAR(50),           -- yapsody_seatsio | external_redirect
    performance_id  VARCHAR(20),
    workspace_key   VARCHAR(100),          -- seatsio public workspace key
    event_key       VARCHAR(100),          -- Performance-{performance_id}
    chart_key       VARCHAR(100),
    pricing_list    JSON,                  -- [{category: KEY, ticketTypes: [{label, price}]}]
    event_status    VARCHAR(50) DEFAULT 'active',
    modified_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE event_inventory (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    event_id            VARCHAR(20),
    event_name          VARCHAR(255),
    venue_name          VARCHAR(255),
    event_datetime      DATETIME,
    objectLabelOrUuid   VARCHAR(255),
    section_name        VARCHAR(100),
    `row`               VARCHAR(50),
    seat_number         DECIMAL(10,2),
    total_price         DECIMAL(10,2),
    status              VARCHAR(20),       -- available | sold (like prekindle_scraper)
    seat_pattern        VARCHAR(50),       -- Consecutive | Even | Odd
    isRestrictedView    TINYINT(1) DEFAULT 0,
    category            VARCHAR(100),
    ticket_type         VARCHAR(100),
    time_frame          INT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    KEY (event_id), KEY (section_name)
);

CREATE TABLE event_inventory_logs (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    event_id        VARCHAR(20),
    event_name      VARCHAR(255),
    venue_name      VARCHAR(255),
    event_datetime  DATETIME,
    ticket_url      TEXT,
    scraper_type    VARCHAR(50),
    time_frame      INT,
    statuscode      INT,
    total_stock     INT,
    stock_by_section JSON,
    error_message   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
