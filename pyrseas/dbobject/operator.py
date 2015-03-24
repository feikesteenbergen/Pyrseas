# -*- coding: utf-8 -*-
"""
    pyrseas.dbobject.operator
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    This module defines two classes: Operator derived from
    DbSchemaObject and OperatorDict derived from DbObjectDict.
"""
from pyrseas.dbobject import DbObjectDict, DbSchemaObject
from pyrseas.dbobject import quote_id, commentable, ownable
from pyrseas.dbobject import split_schema_obj, split_func_args


class Operator(DbSchemaObject):
    """An operator"""

    keylist = ['schema', 'name', 'leftarg', 'rightarg']
    single_extern_file = True
    catalog_table = 'pg_operator'

    def extern_key(self):
        """Return the key to be used in external maps for this operator

        :return: string
        """
        return '%s %s(%s, %s)' % (self.objtype.lower(), self.name,
                                  self.leftarg, self.rightarg)

    def qualname(self):
        """Return the schema-qualified name of the operator

        :return: string

        No qualification is used if the schema is 'public'.
        """
        return self.schema == 'public' and self.name \
            or "%s.%s" % (quote_id(self.schema), self.name)

    def identifier(self):
        """Return a full identifier for an operator object

        :return: string
        """
        return "%s(%s, %s)" % (self.qualname(), self.leftarg, self.rightarg)

    @commentable
    @ownable
    def create(self):
        """Return SQL statements to CREATE or REPLACE the operator

        :return: SQL statements
        """
        opt_clauses = []
        if self.leftarg != 'NONE':
            opt_clauses.append("LEFTARG = %s" % self.leftarg)
        if self.rightarg != 'NONE':
            opt_clauses.append("RIGHTARG = %s" % self.rightarg)
        if hasattr(self, 'commutator'):
            opt_clauses.append("COMMUTATOR = OPERATOR(%s)" % self.commutator)
        if hasattr(self, 'negator'):
            opt_clauses.append("NEGATOR = OPERATOR(%s)" % self.negator)
        if hasattr(self, 'restrict'):
            opt_clauses.append("RESTRICT = %s" % self.restrict)
        if hasattr(self, 'join'):
            opt_clauses.append("JOIN = %s" % self.join)
        if hasattr(self, 'hashes') and self.hashes:
            opt_clauses.append("HASHES")
        if hasattr(self, 'merges') and self.merges:
            opt_clauses.append("MERGES")
        return ["CREATE OPERATOR %s (\n    PROCEDURE = %s%s%s)" % (
                self.qualname(), self.procedure,
                ',\n    ' if opt_clauses else '', ',\n    '.join(opt_clauses))]

    def get_implied_deps(self, db):
        deps = super(Operator, self).get_implied_deps(db)

        # Types may be not found because builtin, or the operator unary
        leftarg = db.types.find(self.leftarg)
        if leftarg:
            deps.add(leftarg)

        rightarg = db.types.find(self.rightarg)
        if rightarg:
            deps.add(rightarg)

        # The function instead we expect it exists
        # TODO: another ugly hack to locate the object
        fschema, fname = split_schema_obj(self.procedure, self.schema)
        fargs = ', '.join(t for t in [self.leftarg, self.rightarg]
            if t != 'NONE')
        if (fschema, fname, fargs) in db.functions:
            func = db.functions[fschema, fname, fargs]
            deps.add(func)

        # This helper function may be a builtin
        if getattr(self, 'restrict', None):
            fschema, fname = split_schema_obj(self.restrict)
            func = db.functions.get((fschema, fname,
                                    "internal, oid, internal, integer"))
            if func:
                deps.add(func)

        return deps

class OperatorDict(DbObjectDict):
    "The collection of operators in a database"

    cls = Operator
    query = \
        """SELECT o.oid,
                  nspname AS schema, oprname AS name, rolname AS owner,
                  oprleft::regtype AS leftarg, oprright::regtype AS rightarg,
                  oprcode AS procedure, oprcom::regoper AS commutator,
                  oprnegate::regoper AS negator, oprrest AS restrict,
                  oprjoin AS join, oprcanhash AS hashes,
                  oprcanmerge AS merges,
                  obj_description(o.oid, 'pg_operator') AS description
           FROM pg_operator o
                JOIN pg_roles r ON (r.oid = oprowner)
                JOIN pg_namespace n ON (oprnamespace = n.oid)
           WHERE (nspname != 'pg_catalog' AND nspname != 'information_schema')
             AND o.oid NOT IN (
                 SELECT objid FROM pg_depend WHERE deptype = 'e'
                              AND classid = 'pg_operator'::regclass)
           ORDER BY nspname, oprname"""

    def _from_catalog(self):
        """Initialize the dictionary of operators by querying the catalogs"""
        for oper in self.fetch():
            oid = oper.oid
            sch, opr, lft, rgt = oper.key()
            if lft == '-':
                lft = oper.leftarg = 'NONE'
            if rgt == '-':
                rgt = oper.rightarg = 'NONE'
            if oper.commutator == '0':
                del oper.commutator
            if oper.negator == '0':
                del oper.negator
            if oper.restrict == '-':
                del oper.restrict
            if oper.join == '-':
                del oper.join
            self.by_oid[oid] = self[(sch, opr, lft, rgt)] \
                = Operator(**oper.__dict__)

    def find(self, oper):
        """Return an operator given its signature

        :param oper: a signature such as '#>=#(hstore,hstore)'

        Return the operator found, else None.
        """
        schema, name = split_schema_obj(oper)
        name, args = split_func_args(name)
        return self.get((schema, name) + tuple(args))

    def from_map(self, schema, inopers):
        """Initalize the dictionary of operators by converting the input map

        :param schema: schema owning the operators
        :param inopers: YAML map defining the operators
        """
        for key in inopers:
            (objtype, spc, opr) = key.partition(' ')
            if spc != ' ' or objtype != 'operator':
                raise KeyError("Unrecognized object type: %s" % key)
            paren = opr.find('(')
            if paren == -1 or opr[-1:] != ')':
                raise KeyError("Invalid operator signature: %s" % opr)
            (leftarg, rightarg) = opr[paren + 1:-1].split(',')
            rightarg = rightarg.lstrip()
            inoper = inopers[key]
            opr = opr[:paren]
            self[(schema.name, opr, leftarg, rightarg)] = oper = Operator(
                schema=schema.name, name=opr, leftarg=leftarg,
                rightarg=rightarg)
            if not inoper:
                raise ValueError("Operator '%s' has no specification" % opr)
            for attr, val in list(inoper.items()):
                setattr(oper, attr, val)
            if 'oldname' in inoper:
                oper.oldname = inoper['oldname']
            if 'description' in inoper:
                oper.description = inoper['description']
