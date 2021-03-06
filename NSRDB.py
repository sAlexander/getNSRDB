#built in libraries
import urllib
from os import system, listdir
from math import ceil, floor
import csv

#third party libraries
import psycopg2
import progressbar
import multiprocessing

def setupdb(DATABASE):
    """
    This Must be run before running insert
    Run this to set up your NSRDB database. There is no harm in running this more than once
    input database should be in the format:
    DATABASE = {'database': 'mynsrdb', 'user': 'tesla'}
    """

    con = psycopg2.connect(**DATABASE)
    cur = con.cursor()

    cur.execute("select exists(SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'nsrdb')")
    schema = cur.fetchone()[0]

    if not schema:
        cur.execute("""
            create schema nsrdb
            """)

    cur.execute("select exists(select * from information_schema.tables where table_name='locations' and table_schema='nsrdb')")
    table = cur.fetchone()[0]

    if not table:
        cur.execute("""
            create table nsrdb.locations
            (
            locationid serial PRIMARY KEY
            ,utcoffset int
            ,geom geometry
            )
            """)
    con.commit()

    cur.execute("select exists(select * from information_schema.tables where table_name='data' and table_schema='nsrdb')")
    table = cur.fetchone()[0]

    if not table:
        cur.execute("""
            create table nsrdb.data
            (
            datatimeutc timestamp without time zone
            ,locationid int
            ,ghi real
            ,dni real
            ,diffuse real
            ,temp real
            ,dewpt real
            ,pressure real
            ,wnddir real
            ,wndspd real
            ,poa real
            ,acout real
            ,CONSTRAINT data_pkey PRIMARY KEY (locationid, datatimeutc)
            )
            """)
    con.commit()

    cur.execute("select exists(select * from information_schema.tables where table_name='zzstaging' and table_schema='nsrdb')")
    table = cur.fetchone()[0]

    if not table:
        cur.execute("""
            create table nsrdb.zzstaging
            (
            year int
            ,month int
            ,day int
            ,hour int
            ,ghi real
            ,dni real
            ,diffuse real
            ,temp real
            ,dewpt real
            ,pressure real
            ,wnddir real
            ,wndspd real
            )
            """)
    con.commit()

    cur.execute("""
    DO $$
    BEGIN

    IF NOT EXISTS (
    SELECT 1
      from pg_indexes
      where schemaname = 'nsrdb'
        and tablename = 'locations'
        and indexname = 'locations_geom'
    ) THEN

    CREATE INDEX locations_geom ON nsrdb.locations using gist (geom);
    END IF;

    END$$;
    """ )
    con.commit()

def download(folder, i = None, threads = None, geos = None):
    """
    Downloads the data files
    Folder is the only required fields. Defines the working folder to download the data files to
    i is if you would like to download every ith data point. Defaults to 1
    threads defaults to 1. It is highly suggested to use a value around 8 for speed
    geos defaults to all of the Lower 48 and Hawaii

    Geos format
    geos = { 'n': 41.00,   # * required
            's': 39.00,   # * required
            'e': -99.00,  # * required
            'w': -101.00, # * required
        }

    example usage : NSRDB.download(folder = '/datawork/tmp', i = 3, threads = 8, geos = geos)

    min lat: 18.05
    max lat: 49.35
    min lon: 44.65
    max lon: 160.95
    """

    if not i:
        i = 4

    if not threads:
        threads = 1

    if not geos:
        geobounds = [1805, 4935, 4465, 16095]
    else:
        geobounds = parsegeos(geos)

    #round to the nearest numer whose last digit is 5
    latmin = 5 * (ceil(((floor(geobounds[0]/5.0)+1)/2))*2-1)
    latmax = 5 * (ceil(((floor(geobounds[1]/5.0)+1)/2))*2-1)
    lonmin = 5 * (ceil(((floor(geobounds[2]/5.0)+1)/2))*2-1)
    lonmax = 5 * (ceil(((floor(geobounds[3]/5.0)+1)/2))*2-1)

    def getdl(t):
        while not q.empty():
            qin = q.get()
            lat = qin[0]
            lon = qin[1]
            url = 'http://mapsdb.nrel.gov/prospector_solar_data/hourly_9809/%0.0f%0.0f/radwx_%05d%0.0f_1999.csv.gz' % (2*int(lon/200), 2*int(lat/200), lon, lat)
            ret = urllib.urlopen(url)
            print url
            if ret.code == 200:
                print 'Downloading point %s %s with thread %s' % (lon, lat, t)
                for y in xrange(1998,2010):
                    url = 'http://mapsdb.nrel.gov/prospector_solar_data/hourly_9809/%0.0f%0.0f/radwx_%05d%0.0f_%s.csv.gz' % (2*int(lon/200), 2*int(lat/200), lon, lat, y)
                    filename = folder + 'radwx_%05d%0.0f_%s.csv.gz' % ( lon*100, lat*100, y )
                    testfile = urllib.URLopener()
                    testfile.retrieve(url, filename)
                    system('gzip -d %s' % filename)
            else:
                print 'Point %s %s not exists thread %s' % (lon, lat, t)

    q = multiprocessing.Queue()

    lat = latmin
    lon = lonmin

    degDelta = i * 10.0

    while lat <= latmax:
        while lon <= lonmax:
            q.put([lat, lon])
            lon += degDelta
        lat += degDelta
        lon = lonmin

    processes = []
    
    for t in xrange(threads):
        p = multiprocessing.Process(target=getdl, args=(t,))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

def parsegeos(geos):
    """
    parses the geos into the bounding array
    """

    results = []

    if isinstance(geos,dict):
        if all (k in geos for k in ('n','s','e','w')):
            results.append(100.0 * geos['s'])
            results.append(100.0 * geos['n'])
            results.append(-100.0 * geos['e'])
            results.append(-100.0 * geos['w'])
        else:
            print 'geos is missing definitions'
            raise Exception('Please include all 4 directions in the geos')
    else:
        print('Geos does not match expected form. See geos doc')
        raise Exception('Unknown geos form')

    return results

def insert(DATABASE, folder):
    """
    Insets the data to your database
    Make sure the folder only has NSRDB files (& no folders) in it otherwise it will error
    Without modification, progressbar will only show steps of 1/100 of progress. It may
    take a while for 1/100 of the import to complete and show progress.
    To change, simply edit line 302 in __init__.py in the progressbar module:
    change: self.num_intervals = max(100, self.term_width)
    to: self.num_intervals = max({put interval here}, self.term_width)
    """
    global con
    global cur

    con = psycopg2.connect(**DATABASE)
    cur = con.cursor()

    files = listdir(folder)

    widgets = ['Processed: ', progressbar.Counter(), '/%s  ' % len(files), progressbar.Percentage(), progressbar.Bar()]
    pbar = progressbar.ProgressBar(widgets = widgets)

    for f in pbar(files):
        fcsv = open(folder + '/' + f, 'rb')
        reader = csv.reader(fcsv)

        row = reader.next()

        lat = row[1]
        lon = row[2]
        utcoffset = row[4]

        cur.execute("""
                    INSERT into nsrdb.locations (utcoffset, geom)
                        SELECT {0} as utcoffset
                        , ST_GeometryFromText('POINT({1} {2})',4326) as geom
                        WHERE not exists (
                                        SELECT 1 from nsrdb.locations loc
                                        WHERE loc.geom = ST_GeometryFromText('POINT({1} {2})',4326)
                                        )
                    """.format(utcoffset, lon, lat))
        con.commit()

        cur.execute("""
                    COPY nsrdb.zzstaging
                    FROM '{0}/{1}'
                    DELIMITER ',' CSV
                    HEADER
                    """.format(folder, f))
        con.commit()

        cur.execute("""
                    INSERT into nsrdb.data (datatimeutc, locationid, ghi, dni, diffuse, temp, dewpt, pressure, wnddir, wndspd)
                        SELECT cast(year || '-' || month  || '-' || day  || ' ' || hour  || ':' as timestamp without time zone) - interval '{0} hours' as datatimeutc
                        ,(  SELECT loc.locationid
                            FROM nsrdb.locations loc
                            WHERE loc.geom = ST_GeometryFromText('POINT({1} {2})',4326)
                         ) as locationid
                        ,ghi
                        ,dni
                        ,diffuse
                        ,temp
                        ,dewpt
                        ,pressure
                        ,wnddir
                        ,wndspd
                        FROM nsrdb.zzstaging
                        WHERE not exists (
                            SELECT 1 from nsrdb.data d2
                            WHERE d2.datatimeutc = cast(year || '-' || month  || '-' || day  || ' ' || hour  || ':' as timestamp without time zone) - interval '{0} hours'
                            AND d2.locationid = (   SELECT loc.locationid
                                                    FROM nsrdb.locations loc
                                                    WHERE loc.geom = ST_GeometryFromText('POINT({1} {2})',4326)
                                                )
                            )
                    """.format(utcoffset, lon, lat))
        con.commit()

        cur.execute(""" delete from nsrdb.zzstaging """)
        con.commit()

        vacuum('nsrdb.zzstaging')
    vacuumfull('nsrdb.zzstaging')

def vacuumfull(table):
    old_isolation_level = con.isolation_level
    con.set_isolation_level(0)
    query = "VACUUM FULL %s" % table
    cur.execute(query)
    con.commit()
    con.set_isolation_level(old_isolation_level)

def vacuum(table):
    old_isolation_level = con.isolation_level
    con.set_isolation_level(0)
    query = "VACUUM %s" % table
    cur.execute(query)
    con.commit()
    con.set_isolation_level(old_isolation_level)

if __name__ == "__main__":
    DATABASE = {'database': 'weather', 'user': 'alex'}
    folder = '/datawork/tmp/nsrdb/'
    geos = { 'n': 41.00,
            's': 37.00,
            'e': -102.00,
            'w': -109.00
            } #Colorado
    setupdb(DATABASE)
    download(folder = folder, i = 8, threads = 8)
    insert(DATABASE,folder)