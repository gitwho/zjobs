
import os
from os.path import dirname, realpath
import sys
import time
app_home_dir = dirname(dirname(realpath(__file__)))
sys.path.append(app_home_dir)  ### setup sys path to use the current app modules

import app.config as config
import pg8000 as dbi
from app.config import logger
from jobcrawler.items import JobItem

from multiprocessing import Pool
import datetime
import time

from apscheduler.triggers.cron import CronTrigger

from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.background import BackgroundScheduler

class Scheduler:   
    scheduler = None
    @staticmethod
    def get_scheduler():
        if Scheduler.scheduler is None:
            Scheduler.scheduler= BackgroundScheduler(logger=logger)
            Scheduler.scheduler.start()
        
        return Scheduler.scheduler

def create_db():
    #conn = sqlite3.connect(config.DB_FILE)
    conn = dbi.connect(host=config.DB_HOST, database=config.DATABASE, user=config.DB_USER, password=config.DB_PASSWORD)
    #conn = dbi.connect('postgres://zjobs:zjobs@localhost:5432/zjobs')
    try:
        c = conn.cursor()

        c.execute('DROP TABLE IF EXISTS CRAWLED_JOBS')
        c.execute('DROP INDEX IF EXISTS job_title_idx')

        logger.info("dropped related tables and indexes")

        # c.execute('''
        #     CREATE TABLE IF NOT EXISTS CRAWLED_JOBS(
        #         source            text,
        #         crawled_date      timestamp,
        #         publish_date      timestamp,
        #         job_title         text,
        #         job_desc          text,
        #         job_details_link  text,
        #         job_location      text,
        #         job_country       text,
        #         salary            text,
        #         employer_name     text,
        #         contact           text
        #     );
        # ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS CRAWLED_JOBS(
                source            text,
                crawled_date      date,
                publish_date      date,
                job_title         text,
                job_desc          text,
                job_details_link  text,
                job_location      text,
                job_country       text,
                salary            text,
                employer_name     text,
                contact           text
            );
            ''')

        logger.info("created related tables")

        c.execute('''
            CREATE UNIQUE INDEX job_title_idx ON CRAWLED_JOBS(job_title)
        ''')

        logger.info("created related indexes")

        conn.commit()
        logger.info('done create database')
    except:
        conn.rollback()
        logger.error('Unable to run create_db')
    finally:
        conn.close()
    
def _crawl(spider_name=None):
        if spider_name:
            os.system('cd %s && scrapy crawl %s' % (app_home_dir, spider_name))
            logger.info('Done running spider %s' % spider_name)
        return None

def run_crawler():
    start_time = time.time()
    logger.info('start running crawler..')

    # os.system('python '+ app_home_dir +'/app/run_crawler.py')
    spider_names = ['sgxin', 'shichengbbs', 'singxin', 'sggongzuo']
    
    pool = Pool(processes=len(spider_names))
    pool.map(_crawl, spider_names)
   
    logger.info('done running crawler.. Time elapsed: %.3fs' % (time.time() - start_time))

def run_web():
    logger.info('starting web..')
    os.system('cd '+ app_home_dir +' && gunicorn -c app/gunicorn.conf.py web.jobboard:app --debug')

def run_flask_web():
    import web.jobboard
    web.jobboard.app.run(host='0.0.0.0', port=config.WEB_HTTP_PORT, debug=config.WEB_DEBUG_ENABLED)

def run_heartbeater():
    import requests

    logger.info('started heartbeating..')
    resp = requests.get(config.APP_HEARTBEAT_URL, headers={'User-Agent': 'Zjobs Heartbeater'})
    logger.info('heartbeater received status_code %s', resp.status_code)
    logger.info('done hearting beating')

def run_housekeeper():

    logger.info('start running housekeeper..')
    JobItem.remove_old_records(retention_days=config.HOUSEKEEPING_RECORD_ORDLER_THAN)
    logger.info('done running housekeeper..')

def extract_file_as_bytes(format='xlsx'):
    import xlsxwriter
    import unicodecsv
    import tempfile

    tmp_file = (tempfile.NamedTemporaryFile(prefix='zjobs.', suffix=('.%s' % format), delete=False)).name      
    
    property_names, rows = JobItem.findall()
    if format.lower() == 'xlsx':
        workbook = xlsxwriter.Workbook(tmp_file, {'default_date_format':'yyyy-mm-dd'})
        worksheet = workbook.add_worksheet('crawled_jobs')
        worksheet.set_column('A:M', 40)

        worksheet.write_row(0, 0, [property_name.upper() for property_name in property_names])

        for rowIdx, row in enumerate(rows):
            worksheet.write_row(rowIdx+1, 0, row)
        
        workbook.close()
    elif format.lower() == 'csv':
        with open(tmp_file, 'w') as f:
            writer = unicodecsv.writer(f, encoding='utf-8')
            writer.writerow([property_name.upper() for property_name in property_names])
            for row in rows:
                writer.writerow(row)
    else:
        os.remove(tmp_file)
        raise Exception("'%s' format is not supported" % format)
    
    file_content = open(tmp_file, 'rb').read()
    os.remove(tmp_file)
    return file_content


def run_emailer():
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email import Encoders
    import smtplib

    logger.info('start sending email to subscribers...')
    smtp = smtplib.SMTP(host=config.SMTP_HOST, port=config.SMTP_PORT)

    try:
        smtp.set_debuglevel(4)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user=config.SMTP_USER, password=config.SMTP_PASSWORD)

        logger.info('established secure connection to smtp server...')

        toaddrs = config.TO_ADDRS
        fromaddr = config.FROM_ADDR

        current_date_string = datetime.datetime.now().strftime('%Y-%m-%d')
        message_subject = "%s:%s" % (config.APP_NAME, current_date_string)
        message_text = "Thank you for subscribing %s. Please find the newly posted jobs as of %s" % (config.APP_NAME, current_date_string)

        msg = MIMEMultipart()
        msg['From'] = fromaddr
        msg['To'] = ','.join(toaddrs)
        msg['Subject'] = message_subject
        msg.attach(MIMEText(message_text))

        part = MIMEBase('application', "octet-stream")
        file_format = 'xlsx'
        part.set_payload(extract_file_as_bytes(file_format))
        logger.info('attached extracted files to the mail...waiting to be sent..')
        Encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment; filename="extracted_jobs_%s.%s"' % (current_date_string, file_format) )
        msg.attach(part)

        smtp.sendmail(fromaddr, toaddrs, msg.as_string())
        logger.info('done sending email to subscribers...')
    except Exception as e:
        logger.error(e)
    finally:
        smtp.quit()



def run_batch_jobs():
    scheduler = Scheduler.get_scheduler()
    scheduler.add_job(func=run_crawler, trigger=CronTrigger(hour='*/08'))
    scheduler.add_job(func=run_housekeeper, trigger=CronTrigger(hour='23', minute='05'))
    scheduler.add_job(func=run_heartbeater, trigger=CronTrigger(minute='*/30'))
    scheduler.add_job(func=run_emailer, trigger=CronTrigger(hour='23', minute='35'))
    

def run_app():
    run_batch_jobs()
    run_web()

def parse_process_args():
    import argparse
    parser = argparse.ArgumentParser('run the app component')
    parser.add_argument('component', nargs='?', default='all', type=str,  
        help='app component to run. [all|web|flask_web|batch_jobs|crawler|housekeeper|heartbeater|emailer]')
    args = parser.parse_args()

    if args.component is None:
        run_app()
    elif args.component == 'all':
        run_app()
    elif args.component == 'batch_jobs':
        run_batch_jobs()
    elif args.component == 'crawler':
        run_crawler()
    elif args.component == 'housekeeper':
        run_housekeeper()
    elif args.component == 'heartbeater':
        run_heartbeater()
    elif args.component == 'web':
        run_web()
    elif args.component == 'flask_web':
        run_flask_web()
    elif args.component == 'create_db':
        create_db()
    elif args.component == 'emailer':
        run_emailer()
    else:
        print 'Invalid Usage: '
        parser.print_help()

if __name__ == '__main__':
    parse_process_args()