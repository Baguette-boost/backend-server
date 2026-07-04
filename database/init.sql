CREATE DATABASE IF NOT EXISTS baguetteboost_service;
USE baguetteboost_service;

-- 1. 보호자 테이블
CREATE TABLE IF NOT EXISTS guardians (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    phone VARCHAR(20) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    expo_token VARCHAR(255) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. 대상 환자 테이블
CREATE TABLE IF NOT EXISTS tracked_persons (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    age INT NOT NULL,
    device_id VARCHAR(100) NOT NULL UNIQUE,
    device_token VARCHAR(255) NOT NULL UNIQUE,
    current_battery INT NOT NULL,
    is_active BOOLEAN NOT NULL,
    base_lat DECIMAL(8, 6) NOT NULL,
    base_lng DECIMAL(9, 6) NOT NULL,
    safe_radius INT NOT NULL,
    is_escaped BOOLEAN DEFAULT false NOT NULL,
    guardian_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (guardian_id) REFERENCES guardians(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. GPS 시계열 로그 테이블 (인덱스 최적화)
CREATE TABLE IF NOT EXISTS gps_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    person_id INT NOT NULL,
    latitude DECIMAL(8, 6) NOT NULL,
    longitude DECIMAL(9, 6) NOT NULL,
    battery INT NOT NULL,
    is_fall_detected BOOLEAN DEFAULT false,
    is_wandering_detected BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (person_id) REFERENCES tracked_persons(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 환자별 동선 조회 최적화를 위한 복합 인덱스
CREATE INDEX idx_person_gps_time ON gps_logs(person_id, created_at DESC);

-- 4. 위험 판정 알림 로그 테이블
CREATE TABLE IF NOT EXISTS alert_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    person_id INT NOT NULL,
    alert_type VARCHAR(10) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (person_id) REFERENCES tracked_persons(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 환자별 최근 알림 조회를 위한 복합 인덱스
CREATE INDEX idx_person_alert_time ON alert_logs(person_id, created_at DESC);