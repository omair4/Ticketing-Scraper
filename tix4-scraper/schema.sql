-- tix4 Scraper — Database Schema
-- Database: tix4

CREATE DATABASE IF NOT EXISTS tix4
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE tix4;

-- -------------------------------------------------------
-- Show catalog (populated by tix4_show_discovery)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_discovery (
    id                       INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    show_slug                VARCHAR(255)  NOT NULL,
    title                    VARCHAR(255),
    category                 VARCHAR(100),
    starting_price_usd       DECIMAL(8,2),
    event_url                VARCHAR(500),
    scraper_type             ENUM('native_seatmap','external_seatmap','native_ga','external_ga','native_ga_p6','js_only_seatmap','no_inventory'),
    external_api_provider_id VARCHAR(10)   DEFAULT '',
    is_active                TINYINT(1)    NOT NULL DEFAULT 1,
    created_at               DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    modified_at              DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_show_slug (show_slug),
    KEY idx_active (is_active),
    KEY idx_scraper_type (scraper_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- -------------------------------------------------------
-- Event schedule (populated by tix4_event_schedule)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_schedule (
    id                INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    show_slug         VARCHAR(255)  NOT NULL,
    event_id_token    VARCHAR(100)  NOT NULL,
    showtime_id       VARCHAR(100),
    event_datetime    DATETIME      NOT NULL,
    buy_url_path      TEXT,
    scraper_type      VARCHAR(50),
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    modified_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_show_event (show_slug, event_id_token),
    KEY idx_show_date (show_slug, event_datetime),
    KEY idx_event_date (event_datetime)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- -------------------------------------------------------
-- Seat inventory (populated by event_inventory_main)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_inventory (
    id                 INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    eventId            VARCHAR(100)  NOT NULL,
    event_name         VARCHAR(255),
    venue_name         VARCHAR(255),
    event_datetime     DATETIME,
    objectLabelOrUuid  VARCHAR(255),
    section_name       VARCHAR(100),
    `row`              VARCHAR(50),
    seat_number        DECIMAL(10,2),
    total_price        DECIMAL(10,2),
    status             VARCHAR(20),
    seat_pattern       VARCHAR(50),
    isRestrictedView   TINYINT(1)    DEFAULT 0,
    category           VARCHAR(100),
    ticket_type        VARCHAR(100),
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
    eventId           VARCHAR(100),
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
