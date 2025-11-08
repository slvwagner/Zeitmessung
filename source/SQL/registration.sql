-- Create the database
CREATE DATABASE IF NOT EXISTS ch367079_race;
USE ch367079_race;

-- Create the participants table
CREATE TABLE IF NOT EXISTS participants (
    Registrierungsnummer INT AUTO_INCREMENT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Name VARCHAR(100) NOT NULL,
    Vorname VARCHAR(100),
    Nickname VARCHAR(100),
    Phone VARCHAR(20),
    'E-mail' VARCHAR(255),
    Kategorie VARCHAR(50),
    Geburtsdatum DATE,
    Gewicht DECIMAL(5,2)
);

-- Insert the sample data
INSERT INTO participants (
    created_at, Name, Vorname, Nickname, Phone, Email, Kategorie, Geburtsdatum, Gewicht
) VALUES 
('2025-09-03 01:22:22', 'Affentranger', 'Nicolas', NULL, NULL, NULL, 'Standard', '1995-05-18', NULL),
('2025-09-06 11:29:43', 'Bernasconi', 'Julia', NULL, NULL, NULL, 'Standard', '2015-01-01', NULL),
('2025-09-06 17:08:53', 'Grönemeier', 'Herbert', NULL, NULL, NULL, 'Pimped', '1965-10-23', NULL),
('2025-09-07 11:46:44', 'Roberts', 'Julia', NULL, NULL, NULL, 'Standard', '1980-09-18', NULL),
('2025-09-07 11:52:15', 'Einstein', 'Albert', NULL, NULL, NULL, 'Pimped', '2015-01-01', NULL),
('2025-09-07 22:23:57', 'Benzko', 'Tim', NULL, NULL, NULL, 'Pimped', '1978-01-07', NULL);