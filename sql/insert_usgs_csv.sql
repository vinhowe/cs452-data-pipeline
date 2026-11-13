INSERT INTO quakes.events
    (id, time, updated, latitude, longitude, depth_km, mag, mag_type, place,
     event_type, status, net, nst, gap, dmin, rms, source)
SELECT
    id,
    parseDateTime64BestEffort(time, 3, 'UTC'),
    parseDateTime64BestEffort(updated, 3, 'UTC'),
    toFloat64(latitude),
    toFloat64(longitude),
    toFloat64OrNull(depth),
    toFloat32OrNull(mag),
    magType,
    place,
    type,
    status,
    net,
    toUInt16OrNull(nst),
    toFloat32OrNull(gap),
    toFloat32OrNull(dmin),
    toFloat32OrNull(rms),
    {source:String}
FROM input('time String, latitude String, longitude String, depth String, mag String,
            magType String, nst String, gap String, dmin String, rms String, net String,
            id String, updated String, place String, type String, horizontalError String,
            depthError String, magError String, magNst String, status String,
            locationSource String, magSource String')
FORMAT CSVWithNames
