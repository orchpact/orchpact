"""Transactional personal balances. Amounts are integer millionths of a point."""
from decimal import Decimal
from fastapi import HTTPException
from ..adapters.database import ident, now, dumps
from ..credits import SCALE, measure, point_micros



def micros(value):
    amount=Decimal(str(value))
    if not amount.is_finite() or abs(amount)>Decimal('1000000000') or amount.as_tuple().exponent < -6:
        raise ValueError('积分须为最多6位小数、绝对值不超过10亿的数字')
    return point_micros(amount)


class Wallets:
    def __init__(self, db):
        self.db = db

    def view(self,c,user):
        row=c.execute('SELECT * FROM credit_wallets WHERE user_id=%s',(user,)).fetchone()
        if not row:raise HTTPException(404,'账号积分不存在')
        return dict(balance=row['balance']/SCALE,held=row['held']/SCALE,available=(row['balance']-row['held'])/SCALE)

    def entry(self,c,user,kind,delta=0,held_delta=0,request=None,operator=None,reason='',key=None,fingerprint=None):
        c.execute('UPDATE credit_wallets SET balance=balance+%s,held=held+%s WHERE user_id=%s',(delta,held_delta,user))
        wallet=c.execute('SELECT * FROM credit_wallets WHERE user_id=%s',(user,)).fetchone()
        c.execute('INSERT INTO credit_ledger VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                  (ident(),user,kind,delta,held_delta,wallet['balance'],wallet['held'],request,operator,reason,key,fingerprint,now()))


    def create(self,c,user):
        c.execute('INSERT INTO credit_wallets(user_id) VALUES(%s)',(user,))

    def require_available(self,c,user,amount=1):
        row=c.execute('SELECT w.*,u.disabled FROM credit_wallets w JOIN users u ON u.id=w.user_id WHERE w.user_id=%s',(user,)).fetchone()
        if not row or row['disabled']:raise ValueError('任务发起账号已停用或积分账户不可用')
        if row['balance']-row['held']<amount:
            raise ValueError(f"积分不足：本次需预留{amount/SCALE:g}积分，当前可用{(row['balance']-row['held'])/SCALE:g}积分；请联系管理员增加积分后重试")

    def reserve(self,c,request,project,amount):
        user=project.get('created_by')
        if not user:return # Platform-administrator work has no personal payer.
        self.require_available(c,user,amount)
        c.execute("INSERT INTO credit_charges VALUES(%s,%s,%s,%s,%s)",(request,user,amount,None,'held'))
        self.entry(c,user,'reserve',held_delta=amount,request=request,reason='调用前预留积分')



    def settle(self,c,request):
        charge=c.execute('SELECT * FROM credit_charges WHERE request_id=%s',(request,)).fetchone()
        if not charge or charge['state']=='settled':return
        row=dict(c.execute('SELECT * FROM requests WHERE id=%s',(request,)).fetchone())
        credit=measure(row)
        if credit['points'] is None:return # Unknown usage stays held across failures/restarts.
        self.finish(c,charge,micros(credit['points']),'按实际用量结算')

    def finish(self,c,charge,amount,reason,operator=None):
        if amount>charge['reserved']:reason+='；用量超过预留，按实际记账，不足部分需补足'
        self.entry(c,charge['user_id'],'settlement',delta=-amount,held_delta=-charge['reserved'],request=charge['request_id'],operator=operator,reason=reason)
        c.execute("UPDATE credit_charges SET state='settled',charged=%s WHERE request_id=%s",(amount,charge['request_id']))

    def adjust(self,user,amount,reason,key,operator):
        delta=micros(amount)
        if not delta:raise ValueError('调整积分不能为0')
        reason=reason.strip()
        if not reason:raise ValueError('请填写积分调整原因')
        fingerprint=dumps([user,delta,reason])
        with self.db.tx() as c:
            old=c.execute('SELECT fingerprint FROM credit_ledger WHERE operation_key=%s',('adjust:'+key,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise ValueError('幂等键已用于不同积分调整')
                return self.view(c,user)
            self.view(c,user)
            row=c.execute('SELECT balance,held FROM credit_wallets WHERE user_id=%s',(user,)).fetchone()
            if delta<0 and row['balance']-row['held']+delta<0:raise ValueError('不能扣减超过可用余额的积分（预留积分不可扣减）')
            self.entry(c,user,'adjustment',delta=delta,operator=operator,reason=reason,key='adjust:'+key,fingerprint=fingerprint)
            return self.view(c,user)

    def reconcile(self,request,amount,reason,operator):
        amount=micros(amount)
        if amount<0 or not reason.strip():raise ValueError('核定积分必须非负且填写原因')
        with self.db.tx() as c:
            charge=c.execute('SELECT * FROM credit_charges WHERE request_id=%s',(request,)).fetchone()
            if not charge:raise HTTPException(404,'积分调用不存在')
            if charge['state']=='settled':
                if charge['charged']!=amount:raise ValueError('该调用已经结算，不能重复核定不同积分')
                return self.view(c,charge['user_id'])
            row=c.execute('SELECT status FROM requests WHERE id=%s',(request,)).fetchone()
            if row['status']=='started':raise ValueError('请求仍在执行，不能人工核定')
            self.finish(c,charge,amount,'人工核定：'+reason.strip(),operator)
            return self.view(c,charge['user_id'])

    def ledger(self,c,user):
        self.view(c,user)
        rows=[]
        for row in c.execute('SELECT * FROM credit_ledger WHERE user_id=%s ORDER BY created DESC,id DESC LIMIT 100',(user,)):
            value=dict(row)
            for field in ('delta','held_delta','balance','held'):value[field]/=SCALE
            value.pop('fingerprint');value.pop('operation_key');rows.append(value)
        return rows
