
import psycopg2
import psycopg2.extensions
import datetime
import threading

import socorro.lib.util as util

#-----------------------------------------------------------------------------------------------------------------
def singleValueSql (aCursor, sql, parameters=None):
  aCursor.execute(sql, parameters)
  result = aCursor.fetchall()
  try:
    return result[0][0]
  except Exception, x:
    raise SQLDidNotReturnSingleValue("%s: %s" % (str(x), sql))

#-----------------------------------------------------------------------------------------------------------------
def singleRowSql (aCursor, sql, parameters=None):
  aCursor.execute(sql, parameters)
  result = aCursor.fetchall()
  try:
    return result[0]
  except Exception, x:
    raise SQLDidNotReturnSingleRow("%s: %s" % (str(x), sql))

#-----------------------------------------------------------------------------------------------------------------
def execute (aCursor, sql, parameters=None):
  aCursor.execute(sql, parameters)
  while True:
    aRow = aCursor.fetchone()
    if aRow is not None:
      yield aRow
    else:
      break

#=================================================================================================================
class LoggingCursor(psycopg2.extensions.cursor):
  """Use as cursor_factory when getting cursor from connection:
  ...
  cursor = connection.cursor(cursor_factory = socorro.lib.pyscopghelper.LoggingCursor)
  cursor.setLogger(someLogger)
  ...
  """
  #-----------------------------------------------------------------------------------------------------------------
  def setLogger(self, logger):
    self.logger = logger
    self.logger.info("Now logging cursor")
  #-----------------------------------------------------------------------------------------------------------------
  def execute(self, sql, args=None):
    try:
      self.logger.info(self.mogrify(sql,args))
    except AttributeError:
      pass
    super(LoggingCursor, self).execute(sql,args)
  #-----------------------------------------------------------------------------------------------------------------
  def executemany(self,sql,args=None):
    try:
      try:
        self.logger.info("%s ..." % (self.mogrify(sql,args[0])))
      except TypeError:
        self.logger.info("%s ..." % (sql))
    except AttributeError:
      pass
    super(LoggingCursor,self).executemany(sql,args)


#=================================================================================================================
class SQLDidNotReturnSingleValue (Exception):
  pass

#=================================================================================================================
class SQLDidNotReturnSingleRow (Exception):
  pass

#=================================================================================================================
class CannotConnectToDatabase(Exception):
  pass

#=================================================================================================================
class Database(object):
  """a simple factory for creating connections for a database.  It doesn't track what it gives out"""
  #-----------------------------------------------------------------------------------------------------------------
  def __init__(self, parameters, logger=None):
    super(Database, self).__init__()
    self.databaseHost = parameters['databaseHost']
    self.databasePort = parameters.setdefault('databasePort', 5432)
    self.databaseUser = parameters['databaseUser']
    self.databasePassword = parameters['databasePassword']
    self.dsn = "host=%s port=%s dbname=%s user=%s password=%s" % (self.databaseHostName,
                                                                  self.databasePort,
                                                                  self.databaseName,
                                                                  self.databaseUserName,
                                                                  self.databasePassword)

    self.logger = parameters.setdefault('logger', None)
    if logger:
      self.logger = logger
    if not self.logger:
      self.logger = util.FakeLogger()

  #-----------------------------------------------------------------------------------------------------------------
  def connection (self):
    self.logger.info("%s - connecting to database", threadName)
    try:
      return psycopg2.connect(self.dsn)
    except Exception, x:
      self.logger.critical("%s - cannot connect to the database", threadName)
      raise CannotConnectToDatabase(x)

#=================================================================================================================
class DatabaseConnectionPool(dict):
  #-----------------------------------------------------------------------------------------------------------------
  def __init__(self, parameters, logger=None):
    super(DatabaseConnectionPool, self).__init__()
    self.database = Database(parameters, logger)
    self.logger = self.database.logger

  #-----------------------------------------------------------------------------------------------------------------
  def connectionWithoutTest(self, name=None):
    """Try to re-use this named connection, else create one and use that"""
    if not name:
      name = threading.currentThread().getName()
    return self.setdefault(name, self.database.connection())

  #-----------------------------------------------------------------------------------------------------------------
  def connection(self, name=None):
    """Like connecionCursorPairNoTest, but test that the specified connection actually works"""
    connection = self.connectionWithoutTest(name)
    try:
      cursor = connection.cursor()
      cursor.execute("select 1")
      cursor.fetchall()
      return connection
    except psycopg2.Error:
      # did the connection time out?
      self.logger.info("%s - trying to re-establish a database connection", threading.currentThread().getName())
      try:
        del self[name]
        connection = self.connectionWithoutTest(name)
        cursor.execute("select 1")
        cursor.fetchall()
        return connection
      except Exception, x:
        self.logger.critical("%s - something's gone horribly wrong with the database connection", threading.currentThread().getName())
        raise CannotConnectToDatabase(x)

  #-----------------------------------------------------------------------------------------------------------------
  def cleanup (self):
    self.logger.debug("%s - killing database connections", threading.currentThread().getName())
    for name, aConnection in self.iteritems():
      try:
        aConnection.close()
        self.logger.debug("%s - connection %s closed", threading.currentThread().getName(), name)
      except psycopg2.InterfaceError:
        self.logger.debug("%s - connection %s already closed", threading.currentThread().getName(), name)
      except:
        util.reportExceptionAndContinue(self.logger)
