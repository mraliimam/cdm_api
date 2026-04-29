from app import db

class CCUnknownUser(db.Model):
    __tablename__ = 'CC_UNKNOWN_USER'
    id = db.Column(db.Integer, primary_key=True)
    UPLOADER_ID = db.Column(db.Integer, primary_key=True)
    AGE = db.Column(db.String(50), nullable=True)
    USER_LOCATION= db.Column(db.String(50), nullable=True)
    GENDER =  db.Column(db.String(50), nullable=True) 
    USUAL_MISTAKE_LEVEL= db.Column(db.String(50), nullable=True)
    DIALECT = db.Column(db.String(50), nullable=True)
    UNK_NAME = db.Column(db.String(50), nullable=True)

    def to_json(self):
        return {
            'id': self.id,
            'UNK_NAME': self.UNK_NAME
           
        }
    
    def return_to_json(self):
        return {
            'id': self.id,
            'UNK_NAME': self.UNK_NAME
           
        }






