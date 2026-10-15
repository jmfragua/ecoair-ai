-- BORRADOR. No aplicado. Fuente: Res. 2254/2017, Tabla 6 (ICA, 24 h, ug/m3) y Tabla 4.
CREATE TABLE IF NOT EXISTS ica_breakpoints (
    id SERIAL PRIMARY KEY,
    standard TEXT NOT NULL DEFAULT 'res2254_2017',
    pollutant TEXT NOT NULL CHECK (pollutant IN ('pm25','pm10')),
    category_code TEXT NOT NULL,
    category_name TEXT NOT NULL,
    color TEXT NOT NULL,
    ica_low INT NOT NULL,
    ica_high INT NOT NULL,
    conc_low NUMERIC(6,1) NOT NULL,
    conc_high NUMERIC(6,1) NOT NULL,
    averaging_hours INT NOT NULL DEFAULT 24,
    source TEXT NOT NULL DEFAULT 'Res. 2254/2017 Tabla 6'
);

INSERT INTO ica_breakpoints (pollutant, category_code, category_name, color, ica_low, ica_high, conc_low, conc_high) VALUES
('pm25','buena','Buena','verde',0,50,0,12),
('pm25','aceptable','Aceptable','amarillo',51,100,13,37),
('pm25','danina_sensibles','Dañina a la salud de grupos sensibles','naranja',101,150,38,55),
('pm25','danina','Dañina a la salud','rojo',151,200,56,150),
('pm25','muy_danina','Muy dañina a la salud','purpura',201,300,151,250),
('pm25','peligrosa','Peligrosa','marron',301,500,251,500),
('pm10','buena','Buena','verde',0,50,0,54),
('pm10','aceptable','Aceptable','amarillo',51,100,55,154),
('pm10','danina_sensibles','Dañina a la salud de grupos sensibles','naranja',101,150,155,254),
('pm10','danina','Dañina a la salud','rojo',151,200,255,354),
('pm10','muy_danina','Muy dañina a la salud','purpura',201,300,355,424),
('pm10','peligrosa','Peligrosa','marron',301,500,425,604);

-- Niveles de prevencion/alerta/emergencia (Tabla 4, 24 h). conc_high NULL = sin tope.
CREATE TABLE IF NOT EXISTS alert_levels (
    id SERIAL PRIMARY KEY,
    pollutant TEXT NOT NULL CHECK (pollutant IN ('pm25','pm10')),
    level_code TEXT NOT NULL,
    level_name TEXT NOT NULL,
    conc_low NUMERIC(6,1) NOT NULL,
    conc_high NUMERIC(6,1),
    source TEXT NOT NULL DEFAULT 'Res. 2254/2017 Tabla 4'
);

INSERT INTO alert_levels (pollutant, level_code, level_name, conc_low, conc_high) VALUES
('pm25','prevencion','Prevención',38,55),
('pm25','alerta','Alerta',56,150),
('pm25','emergencia','Emergencia',151,NULL),
('pm10','prevencion','Prevención',155,254),
('pm10','alerta','Alerta',255,354),
('pm10','emergencia','Emergencia',355,NULL);
