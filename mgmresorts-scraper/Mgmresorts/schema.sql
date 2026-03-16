-- MGM Resorts Scraper — Database Schema
-- Database: mgmresorts

CREATE DATABASE IF NOT EXISTS mgmresorts
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE mgmresorts;

-- -------------------------------------------------------
-- Show catalog (populated by mgmresorts_show_discovery)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_discovery (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    show_id          VARCHAR(255)  NOT NULL,     -- e.g. "ka-by-cirque-du-soleil"
    property_id      VARCHAR(36)   NOT NULL,     -- UUID from MGM API
    show_name        VARCHAR(255),
    venue_name       VARCHAR(255),
    venue_address    VARCHAR(500),
    booking_url      VARCHAR(500),               -- "{property}.mgmresorts.com/book-show/{slug}"
    scraper_type     ENUM('mgm_internal', 'external_redirect', 'unknown') DEFAULT 'mgm_internal',
    product_category VARCHAR(100),
    is_active        TINYINT(1)    NOT NULL DEFAULT 1,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    modified_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_show_id (show_id),
    KEY idx_active (is_active),
    KEY idx_scraper_type (scraper_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- -------------------------------------------------------
-- Event schedule (populated by mgmresorts_event_schedule)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_schedule (
    id               INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    show_id          VARCHAR(255)  NOT NULL,
    property_id      VARCHAR(36)   NOT NULL,
    event_id         VARCHAR(50)   NOT NULL,     -- e.g. "Event_4801603"
    event_code       VARCHAR(20),               -- e.g. "KAG0314M"
    season_id        VARCHAR(50),               -- e.g. "Season_4730022"
    event_date       DATE          NOT NULL,
    event_time       VARCHAR(20)   NOT NULL,    -- e.g. "04:30 PM"
    lowest_price     DECIMAL(8,2),
    offer_available  TINYINT(1)    DEFAULT 0,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    modified_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_event_id (event_id),
    KEY idx_show_date (show_id, event_date),
    KEY idx_event_date (event_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- -------------------------------------------------------
-- Seat inventory (populated by event_inventory_main)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_inventory (
    id                 INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    eventId            VARCHAR(50)   NOT NULL,   -- "Event_4801603"
    event_name         VARCHAR(255),
    venue_name         VARCHAR(255),
    event_datetime     DATETIME,
    objectLabelOrUuid  VARCHAR(100),             -- "101-P-1"
    section_name       VARCHAR(100),
    `row`              VARCHAR(20),
    seat_number        INT,
    total_price        DECIMAL(8,2),
    seat_pattern       ENUM('Consecutive','Odd','Even'),
    isRestrictedView   TINYINT(1)    DEFAULT 0,
    category           VARCHAR(50),              -- priceCode e.g. "DN05"
    ticket_type        VARCHAR(100),
    ada                TINYINT(1)    DEFAULT 0,
    vip                TINYINT(1)    DEFAULT 0,
    time_frame         INT           DEFAULT 0,
    created_at         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    KEY idx_event (eventId),
    KEY idx_event_section (eventId, section_name),
    KEY idx_time_frame (time_frame)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- -------------------------------------------------------
-- Scrape run logs
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_inventory_logs (
    id                INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    eventId           VARCHAR(50),
    event_name        VARCHAR(255),
    venue_name        VARCHAR(255),
    event_datetime    DATETIME,
    ticket_url        VARCHAR(500),
    scraper_type      VARCHAR(50),
    time_frame        INT,
    statuscode        INT,
    total_stock       INT DEFAULT 0,
    stock_by_section  JSON,
    error_message     TEXT,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    KEY idx_event (eventId),
    KEY idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
