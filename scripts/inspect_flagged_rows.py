import duckdb
con = duckdb.connect()
con.execute("CREATE VIEW fires AS SELECT * FROM read_parquet('C:/Users/ASUS/REDZONE/data/processed/harmonized/*.parquet')")
con.sql("SELECT sensor_family, satellite, latitude, longitude, acq_date, acq_time, confidence, frp, daynight, type, data_status FROM fires WHERE frp < 0").show()
con.sql("SELECT sensor_family, ROUND(latitude, 1) AS lat, ROUND(longitude, 1) AS lon, COUNT(*) AS detections, COUNT(DISTINCT acq_date) AS days, STRING_AGG(DISTINCT type, ',') AS types FROM fires WHERE latitude > 75 OR latitude < -60 GROUP BY ALL ORDER BY detections DESC LIMIT 20").show()
con.sql("SELECT sensor_family, data_status, confidence, COUNT(*) AS n FROM fires WHERE frp = 0 GROUP BY ALL ORDER BY ALL").show(max_rows=50)